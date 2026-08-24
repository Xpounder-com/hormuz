from __future__ import annotations

import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import verify_postgres_pitr_recovery as pitr


POSTGRES_IMAGE = "postgres@sha256:" + ("d" * 64)


class PostgresPITRRecoverySummaryTests(unittest.TestCase):
    def _summary(self) -> dict[str, object]:
        return pitr.build_summary(
            database_image=POSTGRES_IMAGE,
            database_version="16.14",
            checks={key: True for key in pitr._CHECK_KEYS},
            durations_ms={key: 0 for key in pitr._DURATION_KEYS},
        )

    def test_summary_is_strict_content_free_and_versioned(self) -> None:
        summary = self._summary()

        pitr.validate_summary(summary)
        encoded = json.dumps(summary, sort_keys=True)
        self.assertEqual(summary["schema_id"], "hormuz.postgresql-pitr-recovery")
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["coverage"], "ephemeral_postgresql_wal_pitr_only")
        self.assertEqual(summary["recovery_target"], "named_restore_point")
        self.assertNotIn("postgresql://", encoded)
        self.assertNotIn("marker-before-target", encoded)
        self.assertNotIn("container", encoded)

    def test_partial_or_unpinned_summary_is_rejected(self) -> None:
        with self.assertRaisesRegex(pitr.PITRRecoveryError, "summary_checks_invalid"):
            pitr.build_summary(
                database_image=POSTGRES_IMAGE,
                database_version="16.14",
                checks={key: True for key in pitr._CHECK_KEYS[:-1]},
                durations_ms={key: 0 for key in pitr._DURATION_KEYS},
            )
        with self.assertRaisesRegex(pitr.PITRRecoveryError, "database_image_not_pinned"):
            pitr.build_summary(
                database_image="postgres:16.14",
                database_version="16.14",
                checks={key: True for key in pitr._CHECK_KEYS},
                durations_ms={key: 0 for key in pitr._DURATION_KEYS},
            )
        with self.assertRaisesRegex(pitr.PITRRecoveryError, "summary_durations_invalid"):
            pitr.build_summary(
                database_image=POSTGRES_IMAGE,
                database_version="16.14",
                checks={key: True for key in pitr._CHECK_KEYS},
                durations_ms={key: 0 for key in pitr._DURATION_KEYS[:-1]},
            )

    def test_unexpected_summary_fields_are_rejected(self) -> None:
        malformed = self._summary()
        malformed["unexpected"] = "not permitted"

        with self.assertRaisesRegex(pitr.PITRRecoveryError, "summary_schema_invalid"):
            pitr.validate_summary(malformed)

    def test_summary_write_is_atomic_owner_only_and_contains_no_fixture_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "summary.json"
            pitr.write_summary(output, self._summary())

            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(list(output.parent.iterdir()), [output])
            saved = json.loads(output.read_text(encoding="utf-8"))
            pitr.validate_summary(saved)
            self.assertNotIn("marker-before-target", output.read_text(encoding="utf-8"))

    def test_existing_output_is_rejected_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "summary.json"
            output.write_text("do-not-replace", encoding="utf-8")

            with self.assertRaisesRegex(pitr.PITRRecoveryError, "summary_output_exists"):
                pitr.write_summary(output, self._summary())
            self.assertEqual(output.read_text(encoding="utf-8"), "do-not-replace")

    def test_write_failure_is_content_free_and_leaves_no_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent_file = Path(temporary) / "not-a-directory"
            parent_file.write_text("fixture", encoding="utf-8")
            output = parent_file / "summary.json"

            with self.assertRaisesRegex(pitr.PITRRecoveryError, "summary_write_failed"):
                pitr.write_summary(output, self._summary())
            self.assertFalse(output.exists())

    def test_main_requires_complete_positive_check_set(self) -> None:
        arguments = [
            "summary",
            "--database-image",
            POSTGRES_IMAGE,
            "--database-version",
            "16.14",
        ]
        for key in pitr._DURATION_KEYS:
            arguments.extend((f"--{key.replace('_', '-')}-ms", "0"))
        arguments.extend(("--output", "/tmp/unused-pitr-summary.json"))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = pitr.main(arguments)

        self.assertEqual(result, 1)
        self.assertEqual(stderr.getvalue(), "PostgreSQL PITR recovery failed: summary_checks_invalid\n")

    def test_promotion_wait_accepts_delayed_promotion(self) -> None:
        states = iter((False, False, True))
        sleep_calls: list[float] = []

        pitr.wait_for_promotion(
            lambda: next(states),
            attempts=4,
            interval_ms=250,
            sleeper=sleep_calls.append,
        )

        self.assertEqual(sleep_calls, [0.25, 0.25])

    def test_promotion_wait_has_a_distinct_content_free_timeout(self) -> None:
        sleep_calls: list[float] = []

        with self.assertRaisesRegex(
            pitr.PITRRecoveryError, "^recovery_target_promotion_timeout$"
        ):
            pitr.wait_for_promotion(
                lambda: False,
                attempts=3,
                interval_ms=100,
                sleeper=sleep_calls.append,
            )

        self.assertEqual(sleep_calls, [0.1, 0.1])

    def test_promotion_probe_uses_only_the_fixed_disposable_target(self) -> None:
        completed = pitr.subprocess.CompletedProcess(
            args=(), returncode=0, stdout="f\n", stderr=""
        )
        with mock.patch.object(
            pitr.subprocess, "run", return_value=completed
        ) as run:
            promoted = pitr._postgres_is_promoted(
                "hormuz-postgres-pitr-recovery-12345"
            )

        self.assertTrue(promoted)
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            (
                "docker",
                "exec",
                "hormuz-postgres-pitr-recovery-12345",
                "psql",
                "--username=postgres",
                "--dbname=hormuz_pitr",
                "--set=ON_ERROR_STOP=on",
                "--tuples-only",
                "--no-align",
                "--command",
                "SELECT pg_is_in_recovery()",
            ),
        )
        self.assertEqual(run.call_args.kwargs["stderr"], pitr.subprocess.DEVNULL)
        with self.assertRaisesRegex(
            pitr.PITRRecoveryError, "promotion_target_invalid"
        ):
            pitr._postgres_is_promoted("customer-postgres")

    def test_promotion_wait_cli_reports_only_the_timeout_code(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(pitr, "_postgres_is_promoted", return_value=False):
            with contextlib.redirect_stderr(stderr):
                result = pitr.main(
                    [
                        "promotion-wait",
                        "--container",
                        "hormuz-postgres-pitr-recovery-12345",
                        "--attempts",
                        "2",
                        "--interval-ms",
                        "0",
                    ]
                )

        self.assertEqual(result, 1)
        self.assertEqual(
            stderr.getvalue(),
            "PostgreSQL PITR recovery failed: recovery_target_promotion_timeout\n",
        )

    def test_wrapper_requires_an_explicit_disposable_pitr_acknowledgement(self) -> None:
        wrapper = Path(__file__).resolve().parents[1] / "tools" / "verify_postgres_pitr_recovery.sh"
        source = wrapper.read_text(encoding="utf-8")

        self.assertIn("I_UNDERSTAND_DISPOSABLE_POSTGRESQL_PITR", source)
        self.assertIn("require_explicit_opt_in", source)
        self.assertIn("io.hormuz.disposable-pitr", source)
        self.assertIn("postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777", source)
        self.assertIn("pg_basebackup", source)
        self.assertIn("pg_create_restore_point", source)
        self.assertIn("pg_current_wal_lsn", source)
        self.assertIn("--command 'SELECT 1'", source)
        self.assertIn("recovery_target_name", source)
        promotion = source.index(
            'run_pitr_tool promotion-wait --container "$RECOVERY_CONTAINER"'
        )
        marker = source.index('marker_value="$(docker exec "$RECOVERY_CONTAINER"')
        restricted_state = source.index("run_recovery_tool verify --runtime-dsn")
        negative_recovery = source.index(
            'start_negative_recovery "$UNREACHABLE_CONTAINER"'
        )
        self.assertLess(promotion, marker)
        self.assertLess(marker, restricted_state)
        self.assertLess(restricted_state, negative_recovery)
        self.assertEqual(source.count("run_pitr_tool promotion-wait"), 1)
        self.assertNotIn("recovery_target_not_promoted", source)
        self.assertIn("remove_disposable_container", source)
        self.assertIn("remove_disposable_work_dir", source)
        self.assertIn("copy_disposable_base_backup", source)
        self.assertIn('chmod 0711 "$WORK_DIR"', source)
        self.assertIn('chmod 0733 "$WAL_ARCHIVE" "$EMPTY_WAL_ARCHIVE"', source)
        self.assertIn("--entrypoint bash", source)
        self.assertIn('docker network create --label "$DISPOSABLE_LABEL=true"', source)
        self.assertIn('require_explicit_opt_in\nif [[ -z "$EVIDENCE_DIR" ]]', source)


if __name__ == "__main__":
    unittest.main()
