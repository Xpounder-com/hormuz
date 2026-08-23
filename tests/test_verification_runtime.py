from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools import _verification_runtime as runtime


ROOT = Path(__file__).resolve().parents[1]


class VerificationRuntimeTests(unittest.TestCase):
    def test_digest_helpers_accept_only_complete_immutable_sha256_values(self) -> None:
        digest = "sha256:" + ("a" * 64)
        self.assertTrue(runtime.is_sha256_digest(digest))
        self.assertTrue(runtime.is_pinned_image_reference(f"postgres@{digest}", image_name="postgres"))
        self.assertFalse(runtime.is_pinned_image_reference(f"other@{digest}", image_name="postgres"))
        for invalid in (
            "a" * 64,
            "sha256:" + ("a" * 63),
            "sha256:" + ("A" * 64),
            digest + "\n",
            f"postgres:{digest}",
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(runtime.is_sha256_digest(invalid))

    def test_hash_helpers_are_streaming_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact.bin"
            artifact.write_bytes(b"hormuz-verification-artifact")
            self.assertEqual(
                runtime.file_sha256(artifact),
                "sha256:3d4ce06921e7fdddbc703b5db899c8aedf6e76846afade2dfacfc79f5b50f3ac",
            )

        first = runtime.canonical_json_sha256({"b": [2, 1], "a": True})
        second = runtime.canonical_json_sha256({"a": True, "b": [2, 1]})
        self.assertEqual(first, second)

    def test_private_json_writer_is_atomic_owner_only_and_cleans_failed_temporary_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "summary.json"
            runtime.write_private_json_evidence(output, {"schema_version": 1, "status": "passed"})

            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                '{"schema_version":1,"status":"passed"}\n',
            )
            self.assertEqual(list(output.parent.iterdir()), [output])

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "summary.json"
            with mock.patch.object(runtime.os, "replace", side_effect=OSError("synthetic write failure")):
                with self.assertRaises(OSError):
                    runtime.write_private_json_evidence(output, {"status": "passed"})
            self.assertFalse(output.exists())
            self.assertEqual(list(output.parent.iterdir()), [])

    def test_container_runner_executes_only_an_explicit_docker_command(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="running\n", stderr="")
        with mock.patch.object(runtime.subprocess, "run", return_value=completed) as run:
            result = runtime.run_container_command(
                ("docker", "inspect", "hormuz-disposable-test"),
                timeout_seconds=7,
            )
        self.assertIs(result, completed)
        run.assert_called_once_with(
            ("docker", "inspect", "hormuz-disposable-test"),
            check=False,
            capture_output=True,
            text=True,
            timeout=7,
        )

        with mock.patch.object(runtime.subprocess, "run", return_value=completed) as run:
            runtime.run_container_command(
                ("docker", "exec", "hormuz-disposable-test", "postgres", "--version"),
                timeout_seconds=5,
                capture_stderr=False,
            )
        run.assert_called_once_with(
            ("docker", "exec", "hormuz-disposable-test", "postgres", "--version"),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )

        with mock.patch.object(runtime.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "container_command_must_use_docker"):
                runtime.run_container_command(("sh", "-c", "true"), timeout_seconds=1)
            run.assert_not_called()

    def test_shell_cleanup_preserves_failure_status_and_requires_disposable_label(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "docker-calls.txt"
            environment = {**os.environ, "CAPTURE": str(capture)}
            script = r'''
source tools/_verification_runtime.sh
docker() {
  if [[ "$1" == "inspect" ]]; then printf 'true\n'; return 0; fi
  printf '%s\n' "$*" >>"$CAPTURE"
}
cleanup() {
  local status=$?
  hormuz_remove_disposable_container hormuz-verification-123 io.hormuz.disposable-test
  exit "$status"
}
trap cleanup EXIT
false
'''
            completed = subprocess.run(
                ("bash", "-c", script),
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(capture.read_text(encoding="utf-8"), "rm --force hormuz-verification-123\n")

            capture.unlink()
            unlabeled = script.replace("printf 'true\\n'", "printf 'false\\n'")
            completed = subprocess.run(
                ("bash", "-c", unlabeled),
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(capture.exists())

    def test_proof_assertions_remain_visible_in_independent_entry_points(self) -> None:
        expected_assertions = {
            "verify_ceph_rgw_custody_conformance.py": (
                "retention_reduction_denied",
                "protected_version_deletion_denied",
                "validate_evidence(evidence)",
            ),
            "verify_ceph_rgw_custody_rotation_recovery.py": (
                "runtime_rotation_capability_denied",
                "altered_encrypted_material_fails_closed",
                "validate_evidence(evidence)",
            ),
            "verify_postgres_interruption_recovery.py": (
                "egress_blocked_during_interruption",
                "no_automatic_provider_replay",
                "validate_summary(summary)",
            ),
            "verify_postgres_pitr_recovery.py": (
                "missing_wal_not_promoted",
                "unreachable_target_not_promoted",
                "validate_summary(summary)",
            ),
            "verify_postgres_backup_restore.py": (
                "partial_recovery_not_promoted",
                "state_fingerprint_matches",
                "_validate_summary",
            ),
            "verify_oci_supply_chain.py": (
                "block_requires_scanner_reported_fixed_version",
                "fixable_blocking",
                "_validate_vulnerability_report",
            ),
        }
        for filename, assertions in expected_assertions.items():
            source = (ROOT / "tools" / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                for assertion in assertions:
                    self.assertIn(assertion, source)

        shared_source = (ROOT / "tools" / "_verification_runtime.py").read_text(encoding="utf-8")
        for assertion in {item for values in expected_assertions.values() for item in values}:
            self.assertNotIn(assertion, shared_source)

        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("!tools/_verification_runtime.py", dockerignore)
        self.assertIn("!tools/verify_ceph_rgw_custody_rotation_recovery.py", dockerignore)

    def test_python_proofs_remain_directly_executable_and_fail_nonzero_on_missing_arguments(self) -> None:
        scripts = (
            "verify_ceph_rgw_custody_conformance.py",
            "verify_ceph_rgw_custody_rotation_recovery.py",
            "verify_postgres_backup_restore.py",
            "verify_postgres_interruption_recovery.py",
            "verify_postgres_pitr_recovery.py",
            "verify_oci_supply_chain.py",
        )
        for filename in scripts:
            path = ROOT / "tools" / filename
            with self.subTest(filename=filename):
                environment = {**os.environ, "PYTHONPATH": str(ROOT)}
                help_result = subprocess.run(
                    (sys.executable, str(path), "--help"),
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                failure_result = subprocess.run(
                    (sys.executable, str(path)),
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(failure_result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
