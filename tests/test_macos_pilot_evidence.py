from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools import client_release_versions
from tools import verify_macos_pilot_evidence as pilot


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "macos_pilot"
EVIDENCE_PATH = FIXTURE_ROOT / "complete-synthetic-v1.json"
ARCHIVE_PATH = FIXTURE_ROOT / "Hormuz-0.1.0-notarized.zip"
PROOF_PATH = FIXTURE_ROOT / "distribution-proof-v1.json"
NOTARIZATION_PATH = FIXTURE_ROOT / "notarization-v1.json"
NOW = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)


class MacPilotEvidenceTests(unittest.TestCase):
    def _json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _inputs(
        self,
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        dict[str, object],
        bytes,
        bytes,
        int,
        str,
    ]:
        archive_payload = ARCHIVE_PATH.read_bytes()
        return (
            self._json(EVIDENCE_PATH),
            self._json(PROOF_PATH),
            self._json(NOTARIZATION_PATH),
            PROOF_PATH.read_bytes(),
            NOTARIZATION_PATH.read_bytes(),
            len(archive_payload),
            hashlib.sha256(archive_payload).hexdigest(),
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
            now=NOW,
        )

    def test_complete_synthetic_fixture_exercises_every_gate_but_never_qualifies(self) -> None:
        result = self._validate(*self._inputs())

        self.assertFalse(result["ready_for_controlled_external_pilot"])
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["reasons"], ["synthetic_fixture"])
        self.assertEqual(result["clean_machine_architectures"], ["arm64", "x86_64"])
        self.assertEqual(result["external_initial_completion_count"], 0)
        self.assertEqual(result["external_returning_completion_count"], 0)

    def test_same_complete_shape_can_qualify_only_as_real_operator_evidence(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["evidence_kind"] = "pilot_qualification"
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertTrue(result["ready_for_controlled_external_pilot"])
        self.assertEqual(result["status"], "ready_for_controlled_external_pilot")
        self.assertEqual(result["reasons"], [])
        self.assertIn("not_external_human_validation", result["nonclaims"])

    def test_exact_archive_bytes_and_proof_digests_are_bound(self) -> None:
        inputs = self._inputs()
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "artifact_archive_binding_invalid"):
            self._validate(*inputs[:-2], inputs[-2] + 1, inputs[-1])

        (
            evidence,
            proof,
            notarization,
            _proof_payload,
            notarization_payload,
            archive_size,
            archive_sha256,
        ) = inputs
        altered_proof_payload = json.dumps(proof, sort_keys=True).encode()
        with self.assertRaisesRegex(pilot.MacPilotEvidenceError, "artifact_proof_binding_invalid"):
            self._validate(
                evidence,
                proof,
                notarization,
                altered_proof_payload,
                notarization_payload,
                archive_size,
                archive_sha256,
            )

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

    def test_both_clean_architectures_require_real_gatekeeper_conditions(self) -> None:
        inputs = list(self._inputs())
        evidence = copy.deepcopy(inputs[0])
        evidence["clean_machine_runs"][1]["developer_tools_absent"] = False  # type: ignore[index]
        inputs[0] = evidence

        result = self._validate(*inputs)

        self.assertEqual(result["clean_machine_architectures"], ["arm64"])
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


if __name__ == "__main__":
    unittest.main()
