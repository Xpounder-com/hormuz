from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path

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
        *,
        state: str = "draft",
    ) -> tuple[Path, Path]:
        candidate = manifest["candidate"]
        custody = manifest["custody"]
        assert isinstance(candidate, dict) and isinstance(custody, dict)
        manifest_payload = manifest_path.read_bytes()
        archive_payload = archive.read_bytes()
        release = {
            "tag_name": (
                custody["release_tag"] if state == "draft" else v1_candidate.FINAL_TAG
            ),
            "target_commitish": candidate["source_commit"],
            "draft": state == "draft",
            "prerelease": False,
            "immutable": state == "published",
            "created_at": "2025-08-27T10:01:00Z",
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
        release_path = directory / f"release-{state}.json"
        immutable_path = directory / f"immutable-{state}.json"
        release_path.write_text(json.dumps(release), encoding="utf-8")
        immutable_path.write_text(
            json.dumps({"enabled": True, "enforced_by_owner": False}),
            encoding="utf-8",
        )
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

    def test_draft_and_published_custody_verify_exact_release_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self._archive(root)
            manifest = self._manifest_value(archive)
            manifest_path = self._write_manifest(root, manifest)
            for state in ("draft", "published"):
                with self.subTest(state=state):
                    release, immutable = self._api_files(
                        root, manifest_path, manifest, archive, state=state
                    )
                    result = v1_candidate.validate_custody(
                        manifest_path,
                        archive,
                        release,
                        immutable,
                        state=state,
                    )
                    self.assertTrue(result["archive_reverified"])
                    self.assertEqual(result["release_immutable"], state == "published")
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
                    manifest_path, archive, release, immutable, state="draft"
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
                    manifest_path, archive, release, immutable, state="draft"
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
                state="draft",
            )
            self.assertEqual(result["status"], "eligible_for_exact_promotion")
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
                    state="draft",
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
                    state="draft",
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
                    state="draft",
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
                    state="draft",
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
                    state="draft",
                )

    def test_freeze_workflow_has_one_build_and_no_final_tag_creation(self) -> None:
        workflow = (ROOT / ".github/workflows/freeze-v1-candidate.yml").read_text()
        self.assertEqual(workflow.count("python -m build --sdist"), 1)
        self.assertIn('[[ "$GITHUB_RUN_ATTEMPT" == "1" ]]', workflow)
        self.assertIn("immutable-releases", workflow)
        self.assertIn('gh release create "$CUSTODY_TAG"', workflow)
        self.assertIn("candidate-v1.0.0-", workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertNotIn("gh release upload", workflow)
        self.assertIn("could not prove $label is absent", workflow)
        self.assertIn("'HTTP 404'", workflow)

    def test_promotion_path_cannot_build_or_replace_assets(self) -> None:
        script = (ROOT / "tools/promote_v1_candidate.sh").read_text()
        self.assertNotIn("python -m build", script)
        self.assertNotIn("pip install build", script)
        self.assertNotIn("gh release upload", script)
        self.assertNotIn("--clobber", script)
        first_gate = script.index('python3 "$tool" promotion')
        tag_push = script.index('git -C "$repository_root" push')
        signed_oci = script.index("wait_for_signed_oci\n", tag_push)
        reverify = script.index('prepublish_dir="$work_dir/prepublish-reverification"')
        publish = script.index('gh release edit "$FINAL_TAG"', reverify)
        self.assertLess(first_gate, tag_push)
        self.assertLess(tag_push, signed_oci)
        self.assertLess(signed_oci, reverify)
        self.assertLess(reverify, publish)
        self.assertIn('gh release verify-asset "$tag"', script)
        self.assertIn("freeze_run_id", script)
        self.assertIn("candidate_tag_manifest_mismatch", script)
        self.assertIn("final_tag_target_or_chronology_invalid", script)
        self.assertIn('Gate evidence: $gate_evidence_digest', script)

    def test_source_distribution_contract_includes_custody_tools(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text()
        verifier = (ROOT / "tools/verify_core_wheel.py").read_text()
        for relative in (
            "tools/v1_candidate.py",
            "tools/promote_v1_candidate.sh",
        ):
            self.assertIn(f"include {relative}", manifest)
            self.assertIn(f'"{relative}"', verifier)


if __name__ == "__main__":
    unittest.main()
