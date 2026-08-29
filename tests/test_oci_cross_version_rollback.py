from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from tools import write_oci_cross_version_rollback_evidence as evidence


ROOT = Path(__file__).resolve().parent.parent


def valid_arguments() -> dict[str, object]:
    return {
        "current_digest": evidence.RELEASES["current"]["digest"],
        "current_tag": evidence.RELEASES["current"]["tag"],
        "current_commit": evidence.RELEASES["current"]["commit"],
        "current_referrers": ",".join(evidence.RELEASES["current"]["referrers"]),
        "current_runtime_ms": 4_200,
        "rollback_digest": evidence.RELEASES["rollback"]["digest"],
        "rollback_tag": evidence.RELEASES["rollback"]["tag"],
        "rollback_commit": evidence.RELEASES["rollback"]["commit"],
        "rollback_referrers": ",".join(
            evidence.RELEASES["rollback"]["referrers"]
        ),
        "rollback_runtime_ms": 3_900,
        "registry_image": evidence.REGISTRY_IMAGE,
        "oras_version": evidence.ORAS_VERSION,
        "cosign_version": evidence.COSIGN_VERSION,
        "docker_version": "29.4.3",
        "evaluated_at": "2026-08-29T22:00:00Z",
    }


class OciCrossVersionRollbackTests(unittest.TestCase):
    def test_summary_binds_exact_releases_referrers_sequence_and_nonclaims(self) -> None:
        summary = evidence.create_summary(**valid_arguments())

        self.assertEqual(summary["schema_id"], evidence.SCHEMA_ID)
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(
            summary["source"]["releases"]["current"]["digest"],
            evidence.RELEASES["current"]["digest"],
        )
        self.assertEqual(
            summary["source"]["releases"]["rollback"][
                "referrer_manifest_digests"
            ],
            list(evidence.RELEASES["rollback"]["referrers"]),
        )
        self.assertEqual(
            summary["runtime"]["sequence"],
            [
                "current_started_ready_and_stopped_cleanly",
                "rollback_selected_by_immutable_digest",
                "rollback_started_ready_and_stopped_cleanly",
            ],
        )
        self.assertEqual(summary["runtime"]["external_provider_calls"], 0)
        self.assertFalse(summary["mutation_boundary"]["artifact_build_performed"])
        self.assertFalse(
            summary["mutation_boundary"]["artifact_resigning_performed"]
        )
        self.assertIn("production_deployment_rollback", summary["nonclaims"])

    def test_release_tool_and_referrer_drift_fail_closed(self) -> None:
        arguments = valid_arguments()
        arguments["current_digest"] = "sha256:" + ("f" * 64)
        with self.assertRaisesRegex(
            evidence.RollbackEvidenceError, "release_identity_mismatch"
        ):
            evidence.create_summary(**arguments)

        arguments = valid_arguments()
        arguments["current_referrers"] = ",".join(
            ["sha256:" + ("a" * 64)] * 3
        )
        with self.assertRaisesRegex(
            evidence.RollbackEvidenceError, "current_referrer_set_invalid"
        ):
            evidence.create_summary(**arguments)

        arguments = valid_arguments()
        replacement = "sha256:" + ("0" * 64)
        arguments["rollback_referrers"] = ",".join(
            sorted(
                [
                    replacement,
                    evidence.RELEASES["rollback"]["referrers"][1],
                    evidence.RELEASES["rollback"]["referrers"][2],
                ]
            )
        )
        with self.assertRaisesRegex(
            evidence.RollbackEvidenceError,
            "rollback_referrer_identity_mismatch",
        ):
            evidence.create_summary(**arguments)

        arguments = valid_arguments()
        arguments["cosign_version"] = "v3.1.4"
        with self.assertRaisesRegex(
            evidence.RollbackEvidenceError, "cosign_version_mismatch"
        ):
            evidence.create_summary(**arguments)

    def test_cli_writes_owner_only_evidence_once(self) -> None:
        arguments = valid_arguments()
        arguments.pop("evaluated_at")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "summary.json"
            argv = []
            for name, value in arguments.items():
                argv.extend((f"--{name.replace('_', '-')}", str(value)))
            argv.extend(("--output", str(output)))

            self.assertEqual(evidence.main(argv), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
            self.assertEqual(evidence.main(argv), 1)

    def test_workflow_is_trusted_manual_read_only_and_bounded(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "oci-cross-version-rollback.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("timeout-minutes: 20", workflow)
        self.assertIn("retention-days: 7", workflow)
        self.assertIn("overwrite: false", workflow)
        self.assertIn(
            "f27adb935022d94df8dc77719c322dda592c78a0d57a6f7dcdd8d900b248c454",
            workflow,
        )

    def test_drill_copies_twice_and_cannot_build_sign_or_move_tags(self) -> None:
        script = (
            ROOT / "tools" / "verify_oci_cross_version_rollback.sh"
        ).read_text(encoding="utf-8")

        self.assertEqual(script.count("oras cp --no-tty --recursive"), 2)
        self.assertEqual(script.count("runtime_milliseconds \"${MIRROR_IMAGE}@"), 2)
        self.assertIn("HORMUZ_OCI_SKIP_BUILD=1", script)
        self.assertIn(
            'cosign_for_registry "${allow_http}" verify-attestation', script
        )
        self.assertIn('cosign_for_registry "${allow_http}" verify', script)
        self.assertNotIn("registry_flags[@]", script)
        self.assertIn("completed=0", script)
        self.assertIn('[[ "${completed}" -ne 1', script)
        self.assertIn("--type cyclonedx", script)
        self.assertIn("--type slsaprovenance1", script)
        self.assertNotIn("docker build", script)
        self.assertNotIn("cosign sign", script)
        self.assertNotIn("cosign attest ", script)
        self.assertNotIn(":latest", script)
        for release in evidence.RELEASES.values():
            self.assertIn(release["digest"], script)
            self.assertIn(release["commit"], script)


if __name__ == "__main__":
    unittest.main()
