from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import call, patch

from tools import client_release_versions
from tools import verify_macos_pilot_evidence as pilot


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "macos_pilot"
EVIDENCE_PATH = FIXTURE_ROOT / "complete-synthetic-v1.json"
ARCHIVE_PATH = FIXTURE_ROOT / "Hormuz-0.1.0-notarized.zip"
PROOF_PATH = FIXTURE_ROOT / "distribution-proof-v2.json"
NOTARIZATION_PATH = FIXTURE_ROOT / "notarization-v1.json"
PREVIOUS_ARCHIVE_PATH = FIXTURE_ROOT / "Hormuz-0.0.9-notarized.zip"
PREVIOUS_PROOF_PATH = FIXTURE_ROOT / "previous-distribution-proof-v2.json"
PREVIOUS_NOTARIZATION_PATH = FIXTURE_ROOT / "previous-notarization-v1.json"
NOW = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)


class MacPilotEvidenceTests(unittest.TestCase):
    def _json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _inputs(self) -> tuple[object, ...]:
        archive_payload = ARCHIVE_PATH.read_bytes()
        previous_archive_payload = PREVIOUS_ARCHIVE_PATH.read_bytes()
        return (
            self._json(EVIDENCE_PATH),
            self._json(PROOF_PATH),
            self._json(NOTARIZATION_PATH),
            PROOF_PATH.read_bytes(),
            NOTARIZATION_PATH.read_bytes(),
            len(archive_payload),
            hashlib.sha256(archive_payload).hexdigest(),
            self._json(PREVIOUS_PROOF_PATH),
            self._json(PREVIOUS_NOTARIZATION_PATH),
            PREVIOUS_PROOF_PATH.read_bytes(),
            PREVIOUS_NOTARIZATION_PATH.read_bytes(),
            len(previous_archive_payload),
            hashlib.sha256(previous_archive_payload).hexdigest(),
        )

    def _validate(
        self,
        evidence: dict[str, object],
        proof: dict[str, object],
        notarization: dict[str, object],
        proof_payload: bytes,
        notarization_payload: bytes,
        archive_size: int,
        archive_sha256: str,
        previous_proof: dict[str, object],
        previous_notarization: dict[str, object],
        previous_proof_payload: bytes,
        previous_notarization_payload: bytes,
        previous_archive_size: int,
        previous_archive_sha256: str,
    ) -> dict[str, object]:
        return pilot.validate_evidence(
            evidence,
            distribution_proof=proof,
            distribution_proof_payload=proof_payload,
            notarization_summary=notarization,
            notarization_summary_payload=notarization_payload,
            archive_path=ARCHIVE_PATH,
            archive_size=archive_size,
            archive_sha256=archive_sha256,
            previous_distribution_proof=previous_proof,
            previous_distribution_proof_payload=previous_proof_payload,
            previous_notarization_summary=previous_notarization,
            previous_notarization_summary_payload=previous_notarization_payload,
            previous_archive_path=PREVIOUS_ARCHIVE_PATH,
            previous_archive_size=previous_archive_size,
            previous_archive_sha256=previous_archive_sha256,
            now=NOW,
        )

    def test_complete_synthetic_fixture_exercises_every_gate_but_never_qualifies(self) -> None:
        with (
            patch.object(pilot, "verify_archive") as platform_verifier,
            patch.object(
                pilot, "_authenticate_distribution_artifact"
            ) as distribution_authenticator,
            patch.object(
                pilot,
                "_authenticate_github_run",
                side_effect=(
                    {
                        "created_at": "2026-09-01T14:00:00Z",
                        "run_started_at": "2026-09-01T14:01:00Z",
                        "updated_at": "2026-09-01T14:30:00Z",
                    },
                    {
                        "created_at": "2026-09-01T14:31:00Z",
                        "run_started_at": "2026-09-01T14:32:00Z",
                        "updated_at": "2026-09-01T15:00:00Z",
                    },
                ),
            ) as run_authenticator,
        ):
            result = self._validate(*self._inputs())

        self.assertFalse(result["ready_for_controlled_external_pilot"])
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["reasons"], ["synthetic_fixture"])
        self.assertEqual(result["clean_machine_architectures"], ["arm64", "x86_64"])
        self.assertEqual(result["external_initial_completion_count"], 0)
        self.assertEqual(result["external_returning_completion_count"], 0)
        platform_verifier.assert_not_called()
        distribution_authenticator.assert_not_called()
        run_authenticator.assert_not_called()

    def test_synthetic_fixture_cannot_be_promoted_by_changing_its_kind(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["evidence_kind"] = "pilot_qualification"
        inputs[0] = evidence

        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError, "distribution_proof_product_identity_invalid"
        ):
            self._validate(*inputs)

    def test_production_shaped_complete_evidence_can_qualify(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        proof = copy.deepcopy(inputs[1])
        previous_proof = copy.deepcopy(inputs[7])
        evidence["evidence_kind"] = "pilot_qualification"
        artifact = evidence["artifact"]  # type: ignore[assignment]
        previous_artifact = evidence["previous_artifact"]  # type: ignore[assignment]
        artifact["source_commit"] = "e" * 40
        artifact["workflow_run_url"] = (
            "https://github.com/Xpounder-com/hormuz/actions/runs/999999"
        )
        artifact["bundle_identifier"] = pilot.PRODUCTION_BUNDLE_IDENTIFIER
        artifact["team_identifier"] = pilot.PRODUCTION_TEAM_IDENTIFIER
        proof["bundle_identifier"] = pilot.PRODUCTION_BUNDLE_IDENTIFIER
        proof["team_identifier"] = pilot.PRODUCTION_TEAM_IDENTIFIER
        signing_authority = (
            "Developer ID Application: Xpounder "
            f"({pilot.PRODUCTION_TEAM_IDENTIFIER})"
        )
        proof["signing_authority"] = signing_authority
        proof["source_commit"] = artifact["source_commit"]
        proof["workflow_run_url"] = artifact["workflow_run_url"]
        previous_artifact["source_commit"] = "f" * 40
        previous_artifact["workflow_run_url"] = (
            "https://github.com/Xpounder-com/hormuz/actions/runs/999998"
        )
        previous_artifact["bundle_identifier"] = pilot.PRODUCTION_BUNDLE_IDENTIFIER
        previous_artifact["team_identifier"] = pilot.PRODUCTION_TEAM_IDENTIFIER
        previous_proof["source_commit"] = previous_artifact["source_commit"]
        previous_proof["workflow_run_url"] = previous_artifact["workflow_run_url"]
        previous_proof["bundle_identifier"] = pilot.PRODUCTION_BUNDLE_IDENTIFIER
        previous_proof["team_identifier"] = pilot.PRODUCTION_TEAM_IDENTIFIER
        previous_proof["signing_authority"] = signing_authority
        evidence["macos_operational_evidence_url"] = (
            "https://github.com/Xpounder-com/hormuz/actions/runs/999995"
        )
        gateway = evidence["hosted_gateway"]  # type: ignore[assignment]
        gateway["evidence_kind"] = "live_external_pilot"
        gateway["source_commit"] = "1" * 40
        gateway["deployment_evidence_url"] = (
            "https://github.com/Xpounder-com/hormuz/actions/runs/999997"
        )
        gateway["recovery_evidence_url"] = (
            "https://github.com/Xpounder-com/hormuz/actions/runs/999996"
        )
        for review in evidence["reviews"].values():  # type: ignore[union-attr]
            review["source_commit"] = artifact["source_commit"]
        proof_payload = (json.dumps(proof, indent=2, sort_keys=True) + "\n").encode()
        previous_proof_payload = (
            json.dumps(previous_proof, indent=2, sort_keys=True) + "\n"
        ).encode()
        artifact["distribution_proof_sha256"] = hashlib.sha256(proof_payload).hexdigest()
        previous_artifact["distribution_proof_sha256"] = hashlib.sha256(
            previous_proof_payload
        ).hexdigest()
        inputs[0] = evidence
        inputs[1] = proof
        inputs[3] = proof_payload
        inputs[7] = previous_proof
        inputs[9] = previous_proof_payload

        with (
            patch.object(
                pilot,
                "verify_archive",
                return_value={
                    "team_identifier": pilot.PRODUCTION_TEAM_IDENTIFIER,
                    "authority": signing_authority,
                },
            ) as platform_verifier,
            patch.object(
                pilot,
                "_authenticate_distribution_artifact",
                side_effect=(
                    {
                        "run_number": 12,
                        "run_attempt": 1,
                        "artifact_created_at": datetime(
                            2026, 9, 1, 14, 0, tzinfo=timezone.utc
                        ),
                        "actor_logins": {"release-owner"},
                    },
                    {
                        "run_number": 11,
                        "run_attempt": 2,
                        "artifact_created_at": datetime(
                            2026, 9, 1, 13, 0, tzinfo=timezone.utc
                        ),
                        "actor_logins": {"release-owner"},
                    },
                ),
            ) as distribution_authenticator,
            patch.object(
                pilot,
                "_authenticate_github_run",
                side_effect=(
                    {
                        "created_at": "2026-09-01T14:00:00Z",
                        "run_started_at": "2026-09-01T14:01:00Z",
                        "updated_at": "2026-09-01T14:30:00Z",
                    },
                    {
                        "created_at": "2026-09-01T14:31:00Z",
                        "run_started_at": "2026-09-01T14:32:00Z",
                        "updated_at": "2026-09-01T15:00:00Z",
                    },
                ),
            ) as run_authenticator,
            patch.object(
                pilot, "_authenticate_review_reference"
            ) as review_authenticator,
            patch.object(
                pilot, "_authenticate_gateway_evidence_artifact"
            ) as gateway_artifact_authenticator,
            patch.object(
                pilot, "_authenticate_macos_operational_evidence"
            ) as macos_operations_authenticator,
        ):
            result = self._validate(*inputs)

        self.assertTrue(result["ready_for_controlled_external_pilot"])
        self.assertEqual(result["status"], "ready_for_controlled_external_pilot")
        self.assertEqual(result["reasons"], [])
        self.assertIn("not_external_human_validation", result["nonclaims"])
        self.assertEqual(platform_verifier.call_count, 2)
        self.assertEqual(distribution_authenticator.call_count, 2)
        self.assertEqual(review_authenticator.call_count, 2)
        self.assertEqual(gateway_artifact_authenticator.call_count, 2)
        macos_operations_authenticator.assert_called_once()
        self.assertEqual(
            macos_operations_authenticator.call_args.args[0],
            evidence["macos_operational_evidence_url"],
        )
        self.assertEqual(
            [item.args[2:] for item in gateway_artifact_authenticator.call_args_list],
            [
                ("deployment", "gateway_deployment"),
                ("qualification", "gateway_recovery"),
            ],
        )
        self.assertEqual(
            run_authenticator.call_args_list,
            [
                call(
                    gateway["deployment_evidence_url"],
                    gateway["source_commit"],
                    pilot.EXTERNAL_PILOT_WORKFLOW,
                    "gateway_deployment",
                ),
                call(
                    gateway["recovery_evidence_url"],
                    gateway["source_commit"],
                    pilot.EXTERNAL_PILOT_WORKFLOW,
                    "gateway_recovery",
                ),
            ],
        )

    def test_authenticated_distribution_artifact_binds_exact_retained_files(self) -> None:
        proof = self._json(PROOF_PATH)
        proof["build"] = "12001"
        proof_payload = (json.dumps(proof, indent=2, sort_keys=True) + "\n").encode()
        notarization_payload = NOTARIZATION_PATH.read_bytes()
        archive_payload = ARCHIVE_PATH.read_bytes()
        artifact_payload = io.BytesIO()
        with zipfile.ZipFile(artifact_payload, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr(f"Hormuz-{proof['version']}-notarized.zip", archive_payload)
            package.writestr("distribution-proof.json", proof_payload)
            package.writestr("notarization.json", notarization_payload)
            package.writestr(f"Hormuz-{proof['version']}.dSYM.zip", b"content-free-dsym")

        run = {
            "id": 12345,
            "run_number": 12,
            "run_attempt": 1,
            "actor": {"login": "release-owner"},
            "triggering_actor": {"login": "release-owner"},
        }
        artifact = {
            "id": 67890,
            "name": f"hormuz-macos-{proof['version']}-12-1",
            "size_in_bytes": len(artifact_payload.getvalue()),
            "expired": False,
            "created_at": "2026-09-01T14:00:00Z",
            "url": "https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/67890",
            "archive_download_url": (
                "https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/67890/zip"
            ),
            "workflow_run": {
                "id": 12345,
                "head_branch": "main",
                "head_sha": proof["source_commit"],
            },
        }
        response = {"total_count": 1, "artifacts": [artifact]}

        def download(
            _artifact_id: int,
            destination: Path,
            _expected_size: int,
            _maximum: int,
            _timeout: float,
            _label: str,
        ) -> None:
            destination.write_bytes(artifact_payload.getvalue())

        with (
            patch.object(pilot, "_authenticate_github_run", return_value=run),
            patch.object(pilot, "_github_api_json", return_value=response),
            patch.object(
                pilot, "_download_github_artifact", side_effect=download
            ) as command,
        ):
            authentication = pilot._authenticate_distribution_artifact(
                proof["workflow_run_url"],
                proof["source_commit"],
                proof,
                proof_payload,
                notarization_payload,
                len(archive_payload),
                hashlib.sha256(archive_payload).hexdigest(),
                "artifact",
            )

        self.assertEqual(authentication["run_number"], 12)
        self.assertEqual(authentication["run_attempt"], 1)
        self.assertEqual(
            authentication["artifact_created_at"],
            datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(authentication["actor_logins"], {"release-owner"})
        command.assert_called_once()
        self.assertEqual(command.call_args.args[0], 67890)
        self.assertEqual(
            command.call_args.args[2:],
            (
                len(artifact_payload.getvalue()),
                pilot._MAX_ACTIONS_ARTIFACT_BYTES,
                120,
                "artifact",
            ),
        )

    def test_authenticated_distribution_artifact_rejects_tampered_or_extra_files(self) -> None:
        proof = self._json(PROOF_PATH)
        proof_payload = PROOF_PATH.read_bytes()
        notarization_payload = NOTARIZATION_PATH.read_bytes()
        archive_payload = ARCHIVE_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            artifact_zip = Path(temporary) / "artifact.zip"
            with zipfile.ZipFile(artifact_zip, "w", zipfile.ZIP_DEFLATED) as package:
                package.writestr(f"Hormuz-{proof['version']}-notarized.zip", archive_payload)
                package.writestr("distribution-proof.json", proof_payload + b"tampered")
                package.writestr("notarization.json", notarization_payload)
            with self.assertRaisesRegex(
                pilot.MacPilotEvidenceError,
                "artifact_github_artifact_binding_invalid",
            ):
                pilot._verify_distribution_artifact_zip(
                    artifact_zip,
                    proof,
                    proof_payload,
                    notarization_payload,
                    len(archive_payload),
                    hashlib.sha256(archive_payload).hexdigest(),
                    "artifact",
                )

            with zipfile.ZipFile(artifact_zip, "a", zipfile.ZIP_DEFLATED) as package:
                package.writestr("unexpected.txt", b"unexpected")
            with self.assertRaisesRegex(
                pilot.MacPilotEvidenceError,
                "artifact_github_artifact_members_invalid",
            ):
                pilot._verify_distribution_artifact_zip(
                    artifact_zip,
                    proof,
                    proof_payload,
                    notarization_payload,
                    len(archive_payload),
                    hashlib.sha256(archive_payload).hexdigest(),
                    "artifact",
                )

    def test_authenticated_distribution_artifact_wraps_corrupt_deflate(self) -> None:
        proof = self._json(PROOF_PATH)
        proof_payload = PROOF_PATH.read_bytes()
        notarization_payload = NOTARIZATION_PATH.read_bytes()
        archive_payload = ARCHIVE_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            artifact_zip = Path(temporary) / "artifact.zip"
            with zipfile.ZipFile(artifact_zip, "w", zipfile.ZIP_DEFLATED) as package:
                package.writestr(
                    f"Hormuz-{proof['version']}-notarized.zip", archive_payload
                )
                package.writestr("distribution-proof.json", proof_payload)
                package.writestr("notarization.json", notarization_payload)
            with (
                patch.object(
                    pilot.zipfile.ZipExtFile,
                    "read",
                    side_effect=zlib.error("corrupt deflate"),
                ),
                self.assertRaisesRegex(
                    pilot.MacPilotEvidenceError,
                    "artifact_github_artifact_zip_invalid",
                ),
            ):
                pilot._verify_distribution_artifact_zip(
                    artifact_zip,
                    proof,
                    proof_payload,
                    notarization_payload,
                    len(archive_payload),
                    hashlib.sha256(archive_payload).hexdigest(),
                    "artifact",
                )

    def test_authenticated_history_requires_consecutive_distribution_runs(self) -> None:
        candidate = {
            "run_number": 12,
            "run_attempt": 1,
            "artifact_created_at": datetime(
                2026, 9, 1, 14, 0, tzinfo=timezone.utc
            ),
        }
        previous = {
            "run_number": 11,
            "run_attempt": 3,
            "artifact_created_at": datetime(
                2026, 9, 1, 13, 0, tzinfo=timezone.utc
            ),
        }
        pilot._validate_authenticated_distribution_history(candidate, previous)

        stale_previous = dict(previous, run_number=10)
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError, "previous_artifact_not_immediate"
        ):
            pilot._validate_authenticated_distribution_history(
                candidate, stale_previous
            )

        rerun_candidate = dict(candidate, run_attempt=2)
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError, "previous_artifact_not_immediate"
        ):
            pilot._validate_authenticated_distribution_history(
                rerun_candidate, previous
            )

    def test_clean_machine_run_must_follow_authenticated_artifact_creation(self) -> None:
        evidence = self._json(EVIDENCE_PATH)
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError,
            "clean_machine_run_0_predates_artifact",
        ):
            pilot._validate_clean_machines(
                evidence["clean_machine_runs"],
                evidence["artifact"]["archive_sha256"],  # type: ignore[index]
                datetime(2026, 9, 1, 15, 30, tzinfo=timezone.utc),
                datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
                [],
            )

    def test_review_binds_candidate_and_authenticated_github_attestation(self) -> None:
        evidence = self._json(EVIDENCE_PATH)
        review = evidence["reviews"]["security"]  # type: ignore[index]
        candidate = evidence["artifact"]  # type: ignore[assignment]
        reasons: list[str] = []
        validated = pilot._validate_review(
            review,
            "security_review",
            candidate["archive_sha256"],
            candidate["source_commit"],
            datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
            reasons,
        )
        self.assertEqual(reasons, [])
        attestation = {
            "schema_id": "hormuz.macos-pilot-review",
            "schema_version": 1,
            "claim_scope": pilot.CLAIM_SCOPE,
            "review_kind": "security",
            "status": "passed",
            "independent_reviewer": True,
            "artifact_sha256": candidate["archive_sha256"],
            "source_commit": candidate["source_commit"],
        }
        response = {
            "id": 9000009,
            "html_url": review["reference"],
            "issue_url": "https://api.github.com/repos/Xpounder-com/hormuz/issues/9",
            "created_at": "2026-09-01T16:20:00Z",
            "updated_at": "2026-09-01T16:30:00Z",
            "body": json.dumps(attestation),
            "user": {"login": "independent-reviewer", "type": "User"},
        }
        with patch.object(
            pilot, "_github_api_json", return_value=response
        ) as github_api:
            pilot._authenticate_review_reference(
                validated,
                "security",
                {"release-owner"},
                datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
                "security_review",
            )
        github_api.assert_called_once_with(
            "repos/Xpounder-com/hormuz/issues/comments/9000009",
            "security_review_github_comment",
        )

        changed = copy.deepcopy(review)
        changed["source_commit"] = "f" * 40
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError,
            "security_review_candidate_binding_invalid",
        ):
            pilot._validate_review(
                changed,
                "security_review",
                candidate["archive_sha256"],
                candidate["source_commit"],
                None,
                datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
                [],
            )

        with (
            patch.object(pilot, "_github_api_json", return_value=response),
            self.assertRaisesRegex(
                pilot.MacPilotEvidenceError,
                "security_review_github_comment_not_trusted",
            ),
        ):
            pilot._authenticate_review_reference(
                validated,
                "security",
                {"independent-reviewer"},
                datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
                "security_review",
            )

    def test_exact_archive_bytes_and_proof_digests_are_bound(self) -> None:
        inputs = list(self._inputs())
        inputs[5] = inputs[5] + 1
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "artifact_archive_binding_invalid"):
            self._validate(*inputs)

        inputs = list(self._inputs())
        evidence, proof = inputs[0], inputs[1]
        altered_proof_payload = json.dumps(proof, sort_keys=True).encode()
        inputs[3] = altered_proof_payload
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "artifact_proof_binding_invalid"):
            self._validate(*inputs)

        inputs = list(self._inputs())
        inputs[12] = "0" * 64
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError, "previous_artifact_archive_binding_invalid"
        ):
            self._validate(*inputs)

        inputs = list(self._inputs())
        inputs[9] = json.dumps(inputs[7], sort_keys=True).encode()
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError, "previous_artifact_proof_binding_invalid"
        ):
            self._validate(*inputs)

        inputs = list(self._inputs())
        inputs[10] = json.dumps(inputs[8], sort_keys=True).encode()
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError, "previous_artifact_proof_binding_invalid"
        ):
            self._validate(*inputs)

    def test_workflow_provenance_is_bound_inside_both_distribution_proofs(self) -> None:
        inputs = list(self._inputs())
        proof = copy.deepcopy(inputs[1])
        proof["source_commit"] = "c" * 40
        proof_payload = (json.dumps(proof, indent=2, sort_keys=True) + "\n").encode()
        evidence = copy.deepcopy(inputs[0])
        evidence["artifact"]["distribution_proof_sha256"] = hashlib.sha256(  # type: ignore[index]
            proof_payload
        ).hexdigest()
        inputs[0] = evidence
        inputs[1] = proof
        inputs[3] = proof_payload

        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "artifact_proof_binding_invalid"):
            self._validate(*inputs)

    def test_distribution_and_notarization_must_be_customer_ready(self) -> None:
        inputs = list(self._inputs())
        proof = copy.deepcopy(inputs[1])
        proof["mode"] = "developer-id"
        inputs[1] = proof
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "distribution_proof_not_ready"):
            self._validate(*inputs)

        inputs = list(self._inputs())
        notarization = copy.deepcopy(inputs[2])
        notarization["issue_count"] = 1
        notarization["issue_severities"] = {"warning": 1}
        inputs[2] = notarization
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "notarization_not_cleanly_accepted"):
            self._validate(*inputs)

    def test_real_distribution_proof_requires_the_pinned_apple_team(self) -> None:
        proof = self._json(PROOF_PATH)
        proof["bundle_identifier"] = pilot.PRODUCTION_BUNDLE_IDENTIFIER
        proof["team_identifier"] = "ZYXWVUTSRQ"
        proof["signing_authority"] = (
            "Developer ID Application: Other Organization (ZYXWVUTSRQ)"
        )

        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError,
            "distribution_proof_product_identity_invalid",
        ):
            pilot._validate_distribution_proof(proof, "pilot_qualification")

    def test_both_clean_architectures_require_real_gatekeeper_conditions(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["clean_machine_runs"][1]["developer_tools_absent"] = False  # type: ignore[index]
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertEqual(result["clean_machine_architectures"], ["arm64"])
        self.assertIn("clean_machine_architecture_coverage_incomplete", result["reasons"])

    def test_no_clean_machine_runs_is_valid_incomplete_evidence(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["clean_machine_runs"] = []
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertEqual(result["status"], "not_ready")
        self.assertIn("clean_machine_architecture_coverage_incomplete", result["reasons"])

    def test_update_rollback_and_keychain_lifecycle_are_required(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        lifecycle = evidence["lifecycle"]  # type: ignore[assignment]
        lifecycle["update_to_build"] = "1"
        lifecycle["server_revocation_denied_session"] = False
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertIn("update_rollback_build_sequence_invalid", result["reasons"])
        self.assertIn("keychain_and_session_lifecycle_incomplete", result["reasons"])

    def test_lifecycle_must_name_the_bound_previous_archive_build(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["lifecycle"]["update_from_build"] = "3"  # type: ignore[index]
        evidence["lifecycle"]["rollback_to_build"] = "3"  # type: ignore[index]
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertIn("update_rollback_build_sequence_invalid", result["reasons"])

    def test_previous_archive_must_come_from_a_distinct_workflow(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        previous_proof = copy.deepcopy(inputs[7])
        workflow_run_url = evidence["artifact"]["workflow_run_url"]  # type: ignore[index]
        evidence["previous_artifact"]["workflow_run_url"] = workflow_run_url  # type: ignore[index]
        previous_proof["workflow_run_url"] = workflow_run_url
        previous_proof_payload = (
            json.dumps(previous_proof, indent=2, sort_keys=True) + "\n"
        ).encode()
        evidence["previous_artifact"]["distribution_proof_sha256"] = hashlib.sha256(  # type: ignore[index]
            previous_proof_payload
        ).hexdigest()
        inputs[0] = evidence
        inputs[7] = previous_proof
        inputs[9] = previous_proof_payload

        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "artifact_history_binding_invalid"):
            self._validate(*inputs)

    def test_signed_client_401_semantics_prevent_duplicate_egress(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["client_auth_recovery"][0]["provider_egress_on_rejected_turn"] = 1  # type: ignore[index]
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertIn("signed_client_401_recovery_incomplete", result["reasons"])
        self.assertEqual(pilot.SUPPORTED_CODEX_VERSION, client_release_versions.SUPPORTED_CODEX_VERSION)
        self.assertEqual(
            pilot.SUPPORTED_CLAUDE_CODE_VERSION,
            client_release_versions.SUPPORTED_CLAUDE_CODE_VERSION,
        )

    def test_no_client_recovery_runs_is_valid_incomplete_evidence(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["client_auth_recovery"] = []
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertEqual(result["status"], "not_ready")
        self.assertIn("signed_client_401_recovery_incomplete", result["reasons"])

    def test_live_gateway_requires_extra_attempt_for_bounded_failover(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        gateway = evidence["hosted_gateway"]  # type: ignore[assignment]
        gateway["provider_attempt_record_count"] = gateway["live_provider_request_count"]
        gateway["cancellation_replay_count"] = 1
        gateway["latency_first_body_byte_sample_count"] = 0
        gateway["availability_sla_claimed"] = True
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertIn("live_provider_failover_evidence_incomplete", result["reasons"])
        self.assertIn(
            "live_streaming_latency_cancellation_evidence_incomplete",
            result["reasons"],
        )
        self.assertIn("unsupported_availability_sla_claimed", result["reasons"])

    def test_gateway_requires_both_protocols_and_one_attempt_per_failover_link(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["hosted_gateway"]["provider_protocols"] = ["openai"]  # type: ignore[index]
        inputs[0] = evidence
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError, "gateway_provider_protocols_invalid"
        ):
            self._validate(*inputs)

        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        gateway = evidence["hosted_gateway"]  # type: ignore[assignment]
        gateway["provider_attempt_record_count"] = 9
        gateway["failover_link_record_count"] = 5
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertIn("live_provider_failover_evidence_incomplete", result["reasons"])

    def test_synthetic_gateway_record_cannot_enter_real_qualification(self) -> None:
        evidence = self._json(EVIDENCE_PATH)
        gateway = evidence["hosted_gateway"]

        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError, "gateway_evidence_kind_invalid"
        ):
            pilot._validate_hosted_gateway(gateway, "pilot_qualification", [])

    def test_github_run_authentication_binds_repository_workflow_and_commit(self) -> None:
        url = "https://github.com/Xpounder-com/hormuz/actions/runs/12345"
        source_commit = "a" * 40
        response = {
            "id": 12345,
            "html_url": url,
            "head_sha": source_commit,
            "head_branch": "main",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "path": pilot.MACOS_DISTRIBUTION_WORKFLOW,
            "repository": {"full_name": "Xpounder-com/hormuz"},
        }
        with patch.object(
            pilot,
            "_command_output_bounded",
            return_value=json.dumps(response).encode(),
        ) as command:
            pilot._authenticate_github_run(
                url,
                source_commit,
                pilot.MACOS_DISTRIBUTION_WORKFLOW,
                "artifact",
            )
        command.assert_called_once_with(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                "repos/Xpounder-com/hormuz/actions/runs/12345",
            ],
            pilot._MAX_FILE_BYTES,
            30,
            "artifact_github_run",
        )

        response["path"] = ".github/workflows/ci.yml"
        with (
            patch.object(
                pilot,
                "_command_output_bounded",
                return_value=json.dumps(response).encode(),
            ),
            self.assertRaisesRegex(
                pilot.MacPilotEvidenceError, "artifact_github_run_not_trusted"
            ),
        ):
            pilot._authenticate_github_run(
                url,
                source_commit,
                pilot.MACOS_DISTRIBUTION_WORKFLOW,
                "artifact",
            )

    def test_gateway_runs_must_complete_before_the_evidence_snapshot(self) -> None:
        generated_at = datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc)
        deployment = {
            "created_at": "2026-09-01T14:00:00Z",
            "run_started_at": "2026-09-01T14:01:00Z",
            "updated_at": "2026-09-01T14:30:00Z",
        }
        recovery = {
            "created_at": "2026-09-01T14:31:00Z",
            "run_started_at": "2026-09-01T14:32:00Z",
            "updated_at": "2026-09-01T15:00:00Z",
        }
        pilot._validate_gateway_run_timeline(
            deployment, recovery, generated_at
        )

        recovery["updated_at"] = "2026-09-01T17:01:00Z"
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError,
            "gateway_recovery_chronology_invalid",
        ):
            pilot._validate_github_run_timeline(
                recovery, generated_at, "gateway_recovery"
            )

        recovery.update(
            {
                "created_at": "2026-09-01T14:10:00Z",
                "run_started_at": "2026-09-01T14:20:00Z",
                "updated_at": "2026-09-01T14:50:00Z",
            }
        )
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError,
            "gateway_run_sequence_invalid",
        ):
            pilot._validate_gateway_run_timeline(
                deployment, recovery, generated_at
            )

    def test_authenticated_gateway_artifact_binds_run_produced_evidence(self) -> None:
        evidence = self._json(EVIDENCE_PATH)
        gateway = copy.deepcopy(evidence["hosted_gateway"])
        gateway["evidence_kind"] = "live_external_pilot"
        deployment_proof = {
            "schema_id": "hormuz.external-pilot-deployment-evidence",
            "schema_version": 1,
            "evidence_kind": gateway["evidence_kind"],
            "profile": gateway["profile"],
            "source_commit": gateway["source_commit"],
            "workflow_run_url": gateway["deployment_evidence_url"],
            "identity_provider": gateway["identity_provider"],
            "provider_protocols": gateway["provider_protocols"],
            "https": gateway["https"],
            "inference_enabled": gateway["inference_enabled"],
            "provider_credentials_server_only": gateway[
                "provider_credentials_server_only"
            ],
            "postgresql_durable": gateway["postgresql_durable"],
            "tenant_rls": gateway["tenant_rls"],
            "durable_sessions": gateway["durable_sessions"],
            "monitoring_configured": gateway["monitoring_configured"],
            "worker_saturation_monitoring": gateway[
                "worker_saturation_monitoring"
            ],
            "postgresql_pool_wait_monitoring": gateway[
                "postgresql_pool_wait_monitoring"
            ],
            "support_path_published": gateway["support_path_published"],
            "single_region_acknowledged": gateway["single_region_acknowledged"],
            "availability_sla_claimed": gateway["availability_sla_claimed"],
            "max_inflight_streams": gateway["max_inflight_streams"],
        }
        pilot._validate_gateway_evidence_payload(
            deployment_proof,
            gateway,
            "deployment",
            "gateway_deployment",
        )
        proof = {
            "schema_id": "hormuz.external-pilot-qualification-evidence",
            "schema_version": 1,
            **gateway,
        }
        proof_payload = (json.dumps(proof, indent=2, sort_keys=True) + "\n").encode()
        artifact_payload = io.BytesIO()
        with zipfile.ZipFile(artifact_payload, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr(
                "external-pilot-qualification-evidence.json", proof_payload
            )
        run = {
            "id": 3,
            "run_number": 7,
            "run_attempt": 1,
            "run_started_at": "2026-09-01T14:00:00Z",
            "updated_at": "2026-09-01T15:00:00Z",
        }
        artifact = {
            "id": 777,
            "name": "hormuz-external-pilot-qualification-7-1",
            "size_in_bytes": len(artifact_payload.getvalue()),
            "expired": False,
            "created_at": "2026-09-01T14:45:00Z",
            "url": "https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/777",
            "archive_download_url": (
                "https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/777/zip"
            ),
            "workflow_run": {
                "id": 3,
                "head_branch": "main",
                "head_sha": gateway["source_commit"],
            },
        }
        response = {"total_count": 1, "artifacts": [artifact]}

        def download(
            _artifact_id: int,
            destination: Path,
            _expected_size: int,
            _maximum: int,
            _timeout: float,
            _label: str,
        ) -> None:
            destination.write_bytes(artifact_payload.getvalue())

        with (
            patch.object(pilot, "_github_api_json", return_value=response),
            patch.object(
                pilot, "_download_github_artifact", side_effect=download
            ) as command,
        ):
            pilot._authenticate_gateway_evidence_artifact(
                run,
                gateway,
                "qualification",
                "gateway_recovery",
            )
        command.assert_called_once()
        self.assertEqual(command.call_args.args[0], 777)

        changed = copy.deepcopy(proof)
        changed["live_provider_request_count"] += 1
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError,
            "gateway_recovery_evidence_binding_invalid",
        ):
            pilot._validate_gateway_evidence_payload(
                changed,
                gateway,
                "qualification",
                "gateway_recovery",
            )

    def test_authenticated_macos_operations_artifact_binds_executed_records(self) -> None:
        evidence = self._json(EVIDENCE_PATH)
        artifact = copy.deepcopy(evidence["artifact"])
        previous_artifact = copy.deepcopy(evidence["previous_artifact"])
        operations_url = evidence["macos_operational_evidence_url"]
        proof = {
            "schema_id": "hormuz.macos-pilot-operations-evidence",
            "schema_version": 1,
            "claim_scope": pilot.CLAIM_SCOPE,
            "source_commit": artifact["source_commit"],
            "workflow_run_url": operations_url,
            "candidate_archive_sha256": artifact["archive_sha256"],
            "candidate_distribution_run_url": artifact["workflow_run_url"],
            "previous_source_commit": previous_artifact["source_commit"],
            "previous_archive_sha256": previous_artifact["archive_sha256"],
            "previous_distribution_run_url": previous_artifact["workflow_run_url"],
            "clean_machine_runs": evidence["clean_machine_runs"],
            "lifecycle": evidence["lifecycle"],
            "client_auth_recovery": evidence["client_auth_recovery"],
        }
        proof_payload = (json.dumps(proof, indent=2, sort_keys=True) + "\n").encode()
        artifact_payload = io.BytesIO()
        with zipfile.ZipFile(artifact_payload, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("macos-pilot-operations-evidence.json", proof_payload)
        run = {
            "id": 5,
            "run_number": 9,
            "run_attempt": 1,
            "created_at": "2026-09-01T14:01:00Z",
            "run_started_at": "2026-09-01T14:05:00Z",
            "updated_at": "2026-09-01T16:30:00Z",
        }
        github_artifact = {
            "id": 888,
            "name": "hormuz-macos-pilot-operations-9-1",
            "size_in_bytes": len(artifact_payload.getvalue()),
            "expired": False,
            "created_at": "2026-09-01T16:15:00Z",
            "url": "https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/888",
            "archive_download_url": (
                "https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/888/zip"
            ),
            "workflow_run": {
                "id": 5,
                "head_branch": "main",
                "head_sha": artifact["source_commit"],
            },
        }

        def download(
            _artifact_id: int,
            destination: Path,
            _expected_size: int,
            _maximum: int,
            _timeout: float,
            _label: str,
        ) -> None:
            destination.write_bytes(artifact_payload.getvalue())

        with (
            patch.object(pilot, "_authenticate_github_run", return_value=run) as run_auth,
            patch.object(
                pilot,
                "_github_api_json",
                return_value={"total_count": 1, "artifacts": [github_artifact]},
            ),
            patch.object(
                pilot, "_download_github_artifact", side_effect=download
            ) as command,
        ):
            pilot._authenticate_macos_operational_evidence(
                operations_url,
                artifact,
                previous_artifact,
                evidence["clean_machine_runs"],
                evidence["lifecycle"],
                evidence["client_auth_recovery"],
                datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc),
                datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
            )
        run_auth.assert_called_once_with(
            operations_url,
            artifact["source_commit"],
            pilot.MACOS_PILOT_OPERATIONS_WORKFLOW,
            "macos_operations",
        )
        self.assertEqual(command.call_args.args[0], 888)

        early_run = copy.deepcopy(run)
        early_run["created_at"] = "2026-09-01T13:55:00Z"
        early_run["run_started_at"] = "2026-09-01T13:59:00Z"
        with (
            patch.object(pilot, "_authenticate_github_run", return_value=early_run),
            self.assertRaisesRegex(
                pilot.MacPilotEvidenceError,
                "macos_operations_predates_artifact",
            ),
        ):
            pilot._authenticate_macos_operational_evidence(
                operations_url,
                artifact,
                previous_artifact,
                evidence["clean_machine_runs"],
                evidence["lifecycle"],
                evidence["client_auth_recovery"],
                datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc),
                datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
            )

        for field, mutate in (
            (
                "clean_machine_runs",
                lambda value: value[0].update({"launch_succeeded": False}),
            ),
            (
                "lifecycle",
                lambda value: value.update({"real_oidc_login": False}),
            ),
            (
                "client_auth_recovery",
                lambda value: value[0].update({"automatic_replay_count": 0}),
            ),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(proof)
                mutate(changed[field])
                with self.assertRaisesRegex(
                    pilot.MacPilotEvidenceError,
                    "macos_operations_evidence_binding_invalid",
                ):
                    pilot._validate_macos_operations_evidence_payload(
                        changed,
                        operations_url,
                        artifact,
                        previous_artifact,
                        evidence["clean_machine_runs"],
                        evidence["lifecycle"],
                        evidence["client_auth_recovery"],
                    )

        wrong_nested_type = copy.deepcopy(proof)
        wrong_nested_type["clean_machine_runs"][0]["developer_tools_absent"] = 1
        with self.assertRaisesRegex(
            pilot.MacPilotEvidenceError,
            "macos_operations_evidence_binding_invalid",
        ):
            pilot._validate_macos_operations_evidence_payload(
                wrong_nested_type,
                operations_url,
                artifact,
                previous_artifact,
                evidence["clean_machine_runs"],
                evidence["lifecycle"],
                evidence["client_auth_recovery"],
            )

    def test_bounded_artifact_stream_stops_before_disk_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            completed = root / "completed.zip"
            pilot._stream_command_to_file(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(b'artifact')",
                ],
                completed,
                len(b"artifact"),
                1024,
                10,
                "artifact_github_artifact",
            )
            self.assertEqual(completed.read_bytes(), b"artifact")

            destination = root / "oversized.zip"
            with self.assertRaisesRegex(
                pilot.MacPilotEvidenceError,
                "artifact_github_artifact_too_large",
            ):
                pilot._stream_command_to_file(
                    [
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.buffer.write(b'x' * 262144)",
                    ],
                    destination,
                    1024,
                    2048,
                    10,
                    "artifact_github_artifact",
                )
            self.assertLessEqual(destination.stat().st_size, 1024)

            with self.assertRaisesRegex(
                pilot.MacPilotEvidenceError,
                "github_metadata_too_large",
            ):
                pilot._command_output_bounded(
                    [
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.buffer.write(b'x' * 262144)",
                    ],
                    1024,
                    10,
                    "github_metadata",
                )

    def test_platform_verification_uses_a_stable_archive_snapshot(self) -> None:
        proof = self._json(PROOF_PATH)
        archive_payload = ARCHIVE_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / ARCHIVE_PATH.name
            destination = root / "private"
            destination.mkdir(mode=0o700)
            source.write_bytes(archive_payload)
            snapshot, size, digest = pilot._snapshot_bounded_regular(
                source,
                pilot._MAX_ARCHIVE_BYTES,
                "archive",
                destination,
            )
            source.write_bytes(b"replacement")
            self.assertEqual(snapshot.read_bytes(), archive_payload)
            self.assertEqual((size, digest), (proof["archive_bytes"], proof["archive_sha256"]))

            def replace_verified_archive(*_args: object) -> dict[str, object]:
                snapshot.write_bytes(b"changed during platform verification")
                return {
                    "team_identifier": pilot.PRODUCTION_TEAM_IDENTIFIER,
                    "authority": proof["signing_authority"],
                }

            with (
                patch.object(
                    pilot, "verify_archive", side_effect=replace_verified_archive
                ),
                self.assertRaisesRegex(
                    pilot.MacPilotEvidenceError,
                    "artifact_platform_archive_changed",
                ),
            ):
                pilot._verify_production_archive(snapshot, proof, "artifact")

    def test_huge_build_strings_fail_as_contract_errors(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["artifact"]["build"] = "9" * 5_000  # type: ignore[index]
        inputs[0] = evidence

        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "artifact_build_invalid"):
            self._validate(*inputs)

        with tempfile.TemporaryDirectory() as temporary:
            evidence_path = Path(temporary) / "huge-build.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = pilot.main(
                    [
                        str(evidence_path),
                        "--archive",
                        str(ARCHIVE_PATH),
                        "--distribution-proof",
                        str(PROOF_PATH),
                        "--notarization-summary",
                        str(NOTARIZATION_PATH),
                        "--previous-archive",
                        str(PREVIOUS_ARCHIVE_PATH),
                        "--previous-distribution-proof",
                        str(PREVIOUS_PROOF_PATH),
                        "--previous-notarization-summary",
                        str(PREVIOUS_NOTARIZATION_PATH),
                        "--allow-synthetic-fixture",
                    ]
                )
        self.assertEqual(result, 2)
        self.assertIn("artifact_build_invalid", stderr.getvalue())

    def test_oversized_json_integer_is_a_documented_malformed_input(self) -> None:
        payload = b'{"schema_version":' + (b"9" * 5_000) + b"}"
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "evidence_json_invalid"):
            pilot._parse_json(payload, "evidence")

        with tempfile.TemporaryDirectory() as temporary:
            evidence_path = Path(temporary) / "oversized-number.json"
            evidence_path.write_bytes(payload)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = pilot.main(
                    [
                        str(evidence_path),
                        "--archive",
                        str(ARCHIVE_PATH),
                        "--distribution-proof",
                        str(PROOF_PATH),
                        "--notarization-summary",
                        str(NOTARIZATION_PATH),
                        "--previous-archive",
                        str(PREVIOUS_ARCHIVE_PATH),
                        "--previous-distribution-proof",
                        str(PREVIOUS_PROOF_PATH),
                        "--previous-notarization-summary",
                        str(PREVIOUS_NOTARIZATION_PATH),
                    ]
                )
        self.assertEqual(result, 2)
        self.assertIn("evidence_json_invalid", stderr.getvalue())

    def test_independent_security_and_accessibility_reviews_are_required(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        review = evidence["reviews"]["security"]  # type: ignore[index]
        review.update(
            {
                "status": "not_started",
                "independent_reviewer": False,
                "reference_type": "none",
                "reference": "none",
                "completed_at": "none",
            }
        )
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertIn("security_review_incomplete", result["reasons"])

    def test_unknown_content_fields_and_duplicate_json_members_fail_closed(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["prompt"] = "must not enter retained evidence"
        inputs[0] = evidence
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "evidence_fields_invalid"):
            self._validate(*inputs)

        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "duplicate_json_member"):
            pilot._parse_json(b'{"schema_id":"one","schema_id":"two"}', "evidence")

    def test_boolean_schema_versions_and_unhashable_enums_fail_as_contract_errors(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["schema_version"] = True
        inputs[0] = evidence
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "schema_version_invalid"):
            self._validate(*inputs)

        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["clean_machine_runs"][0]["architecture"] = {}  # type: ignore[index]
        inputs[0] = evidence
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "architecture_invalid"):
            self._validate(*inputs)

        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["hosted_gateway"]["provider_protocols"] = [{}]  # type: ignore[index]
        inputs[0] = evidence
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "gateway_provider_protocols_invalid"):
            self._validate(*inputs)

        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["open_blockers"] = [{}]
        inputs[0] = evidence
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "open_blockers_invalid"):
            self._validate(*inputs)

        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["reviews"]["security"]["status"] = {}  # type: ignore[index]
        inputs[0] = evidence
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "security_review_status_invalid"):
            self._validate(*inputs)

    def test_cli_requires_explicit_synthetic_flag_and_rejects_symlinks(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = pilot.main(
                [
                    str(EVIDENCE_PATH),
                    "--archive",
                    str(ARCHIVE_PATH),
                    "--distribution-proof",
                    str(PROOF_PATH),
                    "--notarization-summary",
                    str(NOTARIZATION_PATH),
                    "--previous-archive",
                    str(PREVIOUS_ARCHIVE_PATH),
                    "--previous-distribution-proof",
                    str(PREVIOUS_PROOF_PATH),
                    "--previous-notarization-summary",
                    str(PREVIOUS_NOTARIZATION_PATH),
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("synthetic_fixture_not_allowed", stderr.getvalue())

        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / "evidence.json"
            os.symlink(EVIDENCE_PATH, link)
            with self.assertRaisesRegex(
                pilot.MacPilotEvidenceError, "evidence_not_bounded_regular_file"
            ):
                pilot._read_bounded_regular(link, pilot._MAX_FILE_BYTES, "evidence")

    def test_synthetic_override_succeeds_only_when_fixture_is_the_sole_reason(self) -> None:
        evidence = self._json(EVIDENCE_PATH)
        evidence["clean_machine_runs"] = []
        with tempfile.TemporaryDirectory() as temporary:
            evidence_path = Path(temporary) / "incomplete-synthetic.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = pilot.main(
                    [
                        str(evidence_path),
                        "--archive",
                        str(ARCHIVE_PATH),
                        "--distribution-proof",
                        str(PROOF_PATH),
                        "--notarization-summary",
                        str(NOTARIZATION_PATH),
                        "--previous-archive",
                        str(PREVIOUS_ARCHIVE_PATH),
                        "--previous-distribution-proof",
                        str(PREVIOUS_PROOF_PATH),
                        "--previous-notarization-summary",
                        str(PREVIOUS_NOTARIZATION_PATH),
                        "--allow-synthetic-fixture",
                    ]
                )

        self.assertEqual(result, 1)
        self.assertIn("clean_machine_architecture_coverage_incomplete", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
