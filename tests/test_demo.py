from __future__ import annotations

import io
import socket
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from hormuz.cli import main
from hormuz.demo import ProviderFreeDemoError, ProviderFreeDemoResult, run_provider_free_demo


class ProviderFreeDemoTests(unittest.TestCase):
    def test_existing_demo_stdout_contract_is_exactly_preserved(self) -> None:
        output = io.StringIO()
        with (
            mock.patch(
                "hormuz.demo.run_provider_free_demo",
                return_value=ProviderFreeDemoResult(
                    elapsed_seconds=1.25,
                    provider_simulator_calls=3,
                    usage_events=4,
                    security_events=1,
                ),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main(["demo"]), 0)

        self.assertEqual(
            output.getvalue(),
            "Hormuz provider-free governed-policy demo\n"
            "PASS allowed request reached the loopback provider simulator\n"
            "PASS unapproved model was rerouted and output-capped\n"
            "PASS detected secret was redacted before provider egress\n"
            "PASS denied request made no provider call\n"
            "PASS content-free evidence validated: 4 usage events, 1 security event\n"
            "PASS external provider calls: 0 (3 loopback simulator calls)\n"
            "Completed in 1.25 seconds; temporary evidence removed\n",
        )

    def test_documented_command_exercises_gateway_using_loopback_only(self) -> None:
        real_create_connection = socket.create_connection
        destinations: list[tuple[str, int]] = []
        demo_roots_before = set(Path(tempfile.gettempdir()).glob("hormuz-provider-free-*"))

        def loopback_connection(address, *args, **kwargs):
            host, port = address
            self.assertEqual(host, "127.0.0.1")
            destinations.append((host, port))
            return real_create_connection(address, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            missing_config = Path(temporary) / "must-not-be-loaded.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch("socket.create_connection", side_effect=loopback_connection),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = main(["--config", str(missing_config), "demo"])

        self.assertEqual(result, 0, stderr.getvalue())
        self.assertTrue(destinations)
        self.assertEqual(stderr.getvalue(), "")
        output = stdout.getvalue()
        self.assertIn("PASS unapproved model was rerouted and output-capped", output)
        self.assertIn("PASS detected secret was redacted before provider egress", output)
        self.assertIn("PASS denied request made no provider call", output)
        self.assertIn("PASS content-free evidence validated: 4 usage events, 1 security event", output)
        self.assertIn("PASS external provider calls: 0 (3 loopback simulator calls)", output)
        self.assertIn("temporary evidence removed", output)
        self.assertEqual(
            set(Path(tempfile.gettempdir()).glob("hormuz-provider-free-*")),
            demo_roots_before,
        )

    def test_internal_failure_is_content_free_and_removes_temporary_evidence(self) -> None:
        demo_roots_before = set(Path(tempfile.gettempdir()).glob("hormuz-provider-free-*"))
        with mock.patch(
            "hormuz.demo._exercise_gateway",
            side_effect=RuntimeError("synthetic-sensitive-failure-detail"),
        ):
            with self.assertRaises(ProviderFreeDemoError) as raised:
                run_provider_free_demo()

        self.assertEqual(raised.exception.code, "provider_free_demo_internal_failure")
        self.assertNotIn("synthetic-sensitive-failure-detail", str(raised.exception))
        self.assertEqual(
            set(Path(tempfile.gettempdir()).glob("hormuz-provider-free-*")),
            demo_roots_before,
        )


if __name__ == "__main__":
    unittest.main()
