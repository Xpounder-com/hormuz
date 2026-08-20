from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from scripts.postgres_rls_feasibility import (
    CROSS_TENANT_FOREIGN_KEY_SQL,
    CROSS_TENANT_WRITE_SQL,
    DEFAULT_IMAGE,
    FINAL_STARTUP_MARKER,
    PROOF_SQL,
    SETUP_SQL,
    PostgresRLSFeasibilityError,
    _write_evidence,
    run_feasibility,
)


PROOF_OUTPUT = """160010
runtime_role|false|false
workspace_rls_flags|true|true
record_rls_flags|true|true
record_owner|hormuz_owner
missing_context_rows|0
tenant_a_visible|record-a
tenant_a_cross_read|0
reused_after_commit_rows|0
tenant_b_visible|record-b
forced_owner_missing_context_rows|0
"""


class FakeDockerRunner:
    def __init__(
        self,
        *,
        proof_output: str = PROOF_OUTPUT,
        allow_cross_write: bool = False,
        cleanup_succeeds: bool = True,
        canonical_digest_available: bool = True,
        startup_logs_before_complete: int = 0,
        launch_raises: bool = False,
    ) -> None:
        self.proof_output = proof_output
        self.allow_cross_write = allow_cross_write
        self.cleanup_succeeds = cleanup_succeeds
        self.canonical_digest_available = canonical_digest_available
        self.startup_logs_before_complete = startup_logs_before_complete
        self.launch_raises = launch_raises
        self.startup_log_calls = 0
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            if command[3] == DEFAULT_IMAGE and not self.canonical_digest_available:
                return subprocess.CompletedProcess(command, 1, "", "not found")
            return subprocess.CompletedProcess(command, 0, json.dumps([DEFAULT_IMAGE]), "")
        if command[:2] == ["docker", "run"]:
            if self.launch_raises:
                raise subprocess.TimeoutExpired(command, 30)
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        if command[:2] == ["docker", "logs"]:
            self.startup_log_calls += 1
            if self.startup_log_calls <= self.startup_logs_before_complete:
                return subprocess.CompletedProcess(command, 0, "initializing\n", "")
            return subprocess.CompletedProcess(
                command,
                0,
                "",
                FINAL_STARTUP_MARKER + "\n",
            )
        if "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, "ready\n", "")
        if command[:2] == ["docker", "stop"]:
            return subprocess.CompletedProcess(
                command,
                0 if self.cleanup_succeeds else 1,
                "container\n" if self.cleanup_succeeds else "",
                "",
            )
        if command[:3] == ["docker", "container", "inspect"]:
            if self.cleanup_succeeds:
                return subprocess.CompletedProcess(command, 1, "", "not found")
            return subprocess.CompletedProcess(command, 0, "container\n", "")
        if command[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["docker", "exec"] and command[-2] == "-c":
            sql = command[-1]
            if sql == SETUP_SQL:
                return subprocess.CompletedProcess(command, 0, "", "")
            if sql == PROOF_SQL:
                return subprocess.CompletedProcess(command, 0, self.proof_output, "")
            if sql == CROSS_TENANT_WRITE_SQL:
                return subprocess.CompletedProcess(
                    command,
                    0 if self.allow_cross_write else 1,
                    "",
                    "ERROR: new row violates row-level security policy",
                )
            if sql == CROSS_TENANT_FOREIGN_KEY_SQL:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    "ERROR: insert violates foreign key constraint",
                )
        raise AssertionError(f"unexpected command: {command!r}")


class PostgresRLSFeasibilityTests(unittest.TestCase):
    def test_checked_in_evidence_is_content_free_and_non_production(self) -> None:
        evidence_path = (
            Path(__file__).resolve().parents[1]
            / "evidence"
            / "postgres-rls-feasibility-2026-08-20.json"
        )
        value = json.loads(evidence_path.read_text(encoding="utf-8"))

        self.assertEqual(
            value["schema_version"],
            "hormuz.postgres-rls-feasibility.v1",
        )
        self.assertEqual(value["status"], "verified")
        self.assertEqual(value["runner"]["postgres_image"], DEFAULT_IMAGE)
        self.assertEqual(value["scope"]["tenant_count"], 2)
        self.assertTrue(value["scope"]["synthetic_data_only"])
        self.assertFalse(value["scope"]["production_schema_accepted"])
        self.assertFalse(value["scope"]["production_persistence_verified"])
        self.assertTrue(all(value["assurances"].values()))
        serialized = evidence_path.read_text(encoding="utf-8")
        for forbidden in (
            "tenant-a",
            "tenant-b",
            "record-a",
            "record-b",
            "workspace-a",
            "workspace-b",
            "CREATE TABLE",
            "hormuz.tenant_id",
            "POSTGRES_PASSWORD",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_two_tenant_proof_is_content_free_and_cleans_up(self) -> None:
        runner = FakeDockerRunner()
        password = "test-password-that-must-not-be-retained"

        result = run_feasibility(
            runner=runner,
            sleeper=lambda _seconds: None,
            nonce_factory=lambda: "a" * 16,
            password_factory=lambda: password,
        )

        self.assertEqual(
            result["schema_version"],
            "hormuz.postgres-rls-feasibility.v1",
        )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["runner"]["postgres_image"], DEFAULT_IMAGE)
        self.assertEqual(result["runner"]["postgres_server_version_num"], "160010")
        self.assertEqual(result["scope"]["tenant_count"], 2)
        self.assertFalse(result["scope"]["production_schema_accepted"])
        self.assertFalse(result["scope"]["production_persistence_verified"])
        self.assertTrue(all(result["assurances"].values()))
        serialized = json.dumps(result, sort_keys=True)
        for forbidden in (
            password,
            "A-value",
            "B-value",
            "record-a",
            "record-b",
            "hormuz.tenant_id",
            "CREATE TABLE",
            "violates row-level security policy",
        ):
            self.assertNotIn(forbidden, serialized)
        run_command = next(call for call in runner.calls if call[:2] == ["docker", "run"])
        self.assertIn("--network", run_command)
        self.assertIn("none", run_command)
        self.assertIn("--rm", run_command)
        self.assertEqual(run_command[run_command.index("--pull") + 1], "never")
        self.assertTrue(
            all(
                option not in run_command
                for option in ("--publish", "-p", "--volume", "-v", "--mount")
            )
        )
        self.assertFalse(any("--publish" in call or "-p" in call for call in runner.calls))
        self.assertTrue(any(call[:2] == ["docker", "stop"] for call in runner.calls))
        self.assertEqual(runner.calls[-1][:2], ["docker", "ps"])

    def test_verified_local_image_id_is_a_safe_docker_desktop_fallback(self) -> None:
        runner = FakeDockerRunner(canonical_digest_available=False)
        result = run_feasibility(
            runner=runner,
            sleeper=lambda _seconds: None,
            nonce_factory=lambda: "e" * 16,
            password_factory=lambda: "p" * 32,
        )

        local_image_id = DEFAULT_IMAGE.split("@", maxsplit=1)[1]
        self.assertEqual(result["runner"]["docker_runtime_reference"], local_image_id)
        run_command = next(call for call in runner.calls if call[:2] == ["docker", "run"])
        self.assertEqual(run_command[-1], local_image_id)

    def test_setup_waits_for_final_postgres_startup_marker(self) -> None:
        runner = FakeDockerRunner(startup_logs_before_complete=2)
        sleeps: list[float] = []

        run_feasibility(
            runner=runner,
            sleeper=sleeps.append,
            nonce_factory=lambda: "f" * 16,
            password_factory=lambda: "p" * 32,
        )

        self.assertEqual(sleeps, [0.25, 0.25])
        first_ready_index = next(
            index
            for index, call in enumerate(runner.calls)
            if "pg_isready" in call
        )
        log_indexes = [
            index
            for index, call in enumerate(runner.calls)
            if call[:2] == ["docker", "logs"]
        ]
        self.assertGreater(first_ready_index, max(log_indexes))

    def test_invalid_image_and_proof_fail_with_stable_codes(self) -> None:
        runner = FakeDockerRunner()
        with self.assertRaisesRegex(PostgresRLSFeasibilityError, "invalid_postgres_image"):
            run_feasibility(image="postgres:latest", runner=runner)
        self.assertEqual(runner.calls, [])

        bad_proof = FakeDockerRunner(proof_output="160010\ntenant_a_visible|record-a\n")
        with self.assertRaisesRegex(PostgresRLSFeasibilityError, "rls_observation_mismatch"):
            run_feasibility(
                runner=bad_proof,
                sleeper=lambda _seconds: None,
                nonce_factory=lambda: "b" * 16,
                password_factory=lambda: "p" * 32,
            )
        self.assertTrue(any(call[:2] == ["docker", "stop"] for call in bad_proof.calls))

        duplicate_proof = FakeDockerRunner(
            proof_output=PROOF_OUTPUT + "runtime_role|false|false\n"
        )
        with self.assertRaisesRegex(PostgresRLSFeasibilityError, "rls_observation_mismatch"):
            run_feasibility(
                runner=duplicate_proof,
                sleeper=lambda _seconds: None,
                nonce_factory=lambda: "1" * 16,
                password_factory=lambda: "p" * 32,
            )

    def test_unknown_launch_outcome_still_triggers_cleanup(self) -> None:
        runner = FakeDockerRunner(launch_raises=True)

        with self.assertRaisesRegex(PostgresRLSFeasibilityError, "docker_unavailable"):
            run_feasibility(
                runner=runner,
                sleeper=lambda _seconds: None,
                nonce_factory=lambda: "2" * 16,
                password_factory=lambda: "p" * 32,
            )

        self.assertTrue(any(call[:2] == ["docker", "stop"] for call in runner.calls))
        self.assertEqual(runner.calls[-1][:2], ["docker", "ps"])

    def test_cross_tenant_write_or_cleanup_failure_cannot_verify(self) -> None:
        allowed = FakeDockerRunner(allow_cross_write=True)
        with self.assertRaisesRegex(
            PostgresRLSFeasibilityError,
            "cross_tenant_write_not_denied",
        ):
            run_feasibility(
                runner=allowed,
                sleeper=lambda _seconds: None,
                nonce_factory=lambda: "c" * 16,
                password_factory=lambda: "p" * 32,
            )

        cleanup_failed = FakeDockerRunner(cleanup_succeeds=False)
        with self.assertRaisesRegex(
            PostgresRLSFeasibilityError,
            "container_cleanup_failed",
        ):
            run_feasibility(
                runner=cleanup_failed,
                sleeper=lambda _seconds: None,
                nonce_factory=lambda: "d" * 16,
                password_factory=lambda: "p" * 32,
            )

    def test_evidence_is_private_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "postgres-rls.json"
            value = {"schema_version": "test", "status": "verified"}
            _write_evidence(value, str(output), force=False)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            original = output.read_bytes()
            with self.assertRaisesRegex(
                PostgresRLSFeasibilityError,
                "evidence_open_failed",
            ):
                _write_evidence(value, str(output), force=False)
            self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
