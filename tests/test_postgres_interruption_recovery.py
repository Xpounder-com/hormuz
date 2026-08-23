from __future__ import annotations

import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools import verify_postgres_interruption_recovery as interruption


POSTGRES_IMAGE = "postgres@sha256:" + ("c" * 64)


class PostgresInterruptionRecoverySummaryTests(unittest.TestCase):
    def _summary(self) -> dict[str, object]:
        return interruption.build_summary(
            database_image=POSTGRES_IMAGE,
            database_version="16.14",
            checks={key: True for key in interruption._CHECK_KEYS},
            durations_ms={key: 0 for key in interruption._DURATION_KEYS},
        )

    def test_summary_is_strict_content_free_and_versioned(self) -> None:
        summary = self._summary()

        interruption.validate_summary(summary)
        encoded = json.dumps(summary, sort_keys=True)
        self.assertEqual(summary["schema_id"], "hormuz.postgresql-interruption-recovery")
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["coverage"], "ephemeral_single_database_abrupt_interrupt_restart_only")
        self.assertNotIn("postgresql://", encoded)
        self.assertNotIn("container", encoded)
        self.assertNotIn("token", encoded)
        self.assertNotIn("request must not reach provider", encoded)

    def test_partial_or_unpinned_summary_is_rejected(self) -> None:
        with self.assertRaisesRegex(interruption.InterruptionRecoveryError, "summary_checks_invalid"):
            interruption.build_summary(
                database_image=POSTGRES_IMAGE,
                database_version="16.14",
                checks={key: True for key in interruption._CHECK_KEYS[:-1]},
                durations_ms={key: 0 for key in interruption._DURATION_KEYS},
            )

        with self.assertRaisesRegex(interruption.InterruptionRecoveryError, "database_image_not_pinned"):
            interruption.build_summary(
                database_image="postgres:16.14",
                database_version="16.14",
                checks={key: True for key in interruption._CHECK_KEYS},
                durations_ms={key: 0 for key in interruption._DURATION_KEYS},
            )

        malformed = self._summary()
        malformed["unexpected"] = "not allowed"
        with self.assertRaisesRegex(interruption.InterruptionRecoveryError, "summary_schema_invalid"):
            interruption.validate_summary(malformed)

    def test_summary_write_is_atomic_owner_only_and_contains_no_fixture_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence" / "summary.json"
            interruption.write_summary(output, self._summary())

            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(list(output.parent.iterdir()), [output])
            saved = json.loads(output.read_text(encoding="utf-8"))
            interruption.validate_summary(saved)
            self.assertNotIn("interruption-fixture", output.read_text(encoding="utf-8"))

    def test_summary_write_failure_stays_content_free_and_leaves_no_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent_file = Path(temporary) / "not-a-directory"
            parent_file.write_text("fixture", encoding="utf-8")
            output = parent_file / "summary.json"

            with self.assertRaisesRegex(interruption.InterruptionRecoveryError, "summary_write_failed"):
                interruption.write_summary(output, self._summary())
            self.assertFalse(output.exists())

    def test_only_an_explicitly_named_disposable_container_can_be_targeted(self) -> None:
        accepted = "hormuz-postgres-interruption-12345678"
        self.assertEqual(interruption.validate_disposable_container_name(accepted), accepted)
        for value in ("postgres", "hormuz-postgres-interruption-short", "hormuz-postgres-source-12345678"):
            with self.assertRaisesRegex(interruption.InterruptionRecoveryError, "container_not_disposable"):
                interruption.validate_disposable_container_name(value)

    def test_database_target_must_have_the_disposable_label_and_declared_version(self) -> None:
        container = "hormuz-postgres-interruption-12345678"
        with mock.patch.object(
            interruption,
            "_docker",
            side_effect=(
                SimpleNamespace(stdout="true\n"),
                SimpleNamespace(stdout=f"{POSTGRES_IMAGE}\n"),
                SimpleNamespace(stdout="postgres (PostgreSQL) 16.14 (Debian 16.14-1.pgdg120+2)\n"),
            ),
        ) as docker:
            interruption._assert_disposable_container(container)
            interruption._assert_database_image(container, expected_image=POSTGRES_IMAGE)
            interruption._assert_database_version(container, expected_version="16.14")

        self.assertEqual(
            docker.call_args_list[0].args[0],
            ("inspect", "--format", '{{ index .Config.Labels "io.hormuz.disposable-interruption" }}', container),
        )
        self.assertEqual(docker.call_args_list[1].args[0], ("inspect", "--format", "{{ .Config.Image }}", container))
        self.assertEqual(docker.call_args_list[2].args[0], ("exec", container, "postgres", "--version"))
        with mock.patch.object(interruption, "_docker", return_value=SimpleNamespace(stdout="postgres:16.14\n")):
            with self.assertRaisesRegex(interruption.InterruptionRecoveryError, "database_image_mismatch"):
                interruption._assert_database_image(container, expected_image=POSTGRES_IMAGE)
        with mock.patch.object(interruption, "_docker", return_value=SimpleNamespace(stdout="postgres (PostgreSQL) 16.13\n")):
            with self.assertRaisesRegex(interruption.InterruptionRecoveryError, "database_version_mismatch"):
                interruption._assert_database_version(container, expected_version="16.14")

    def test_main_requires_explicit_opt_in_before_any_docker_command(self) -> None:
        stderr = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), contextlib.redirect_stderr(stderr):
            result = interruption.main(
                [
                    "run",
                    "--container",
                    "hormuz-postgres-interruption-12345678",
                    "--database-image",
                    POSTGRES_IMAGE,
                    "--database-version",
                    "16.14",
                    "--evidence-out",
                    "/tmp/unused-summary.json",
                ]
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            stderr.getvalue(),
            "PostgreSQL interruption recovery failed: interruption_opt_in_required\n",
        )

    def test_wrapper_pins_one_loopback_port_across_the_container_restart(self) -> None:
        wrapper = Path(__file__).resolve().parents[1] / "tools" / "verify_postgres_interruption_recovery.sh"
        source = wrapper.read_text(encoding="utf-8")

        self.assertIn("printf '%05d%05d%05d'", source)
        self.assertIn("select_loopback_port()", source)
        self.assertIn("start_disposable_postgres()", source)
        self.assertIn('listener.bind(("127.0.0.1", 0))', source)
        self.assertIn('--publish "127.0.0.1:${host_port}:5432"', source)
        self.assertIn('if [[ "$(published_host_port)" != "${host_port}" ]]', source)


if __name__ == "__main__":
    unittest.main()
