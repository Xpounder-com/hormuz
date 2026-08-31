from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "verify_helm_profile.sh"


def shell_function(name: str) -> str:
    source = RUNNER.read_text(encoding="utf-8")
    start = source.index(f"{name}() {{\n")
    return source[start : source.index("\n}\n", start) + 3]


class HelmRunnerDiagnosticsTests(unittest.TestCase):
    def run_shell(self, body: str) -> subprocess.CompletedProcess[str]:
        source = RUNNER.read_text(encoding="utf-8")
        setup = next(line for line in source.splitlines() if line.startswith("set -"))
        error_trap = next(
            line for line in source.splitlines()
            if line.startswith("trap ") and line.endswith(" ERR")
        )
        script = "\n".join(
            (setup, shell_function("report_command_failure"), error_trap, body)
        )
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env={**os.environ, "BASH_ENV": "/dev/null"},
        )

    def test_top_level_failure_keeps_original_exit_status(self) -> None:
        result = self.run_shell('bash -c "exit 37"\nprintf "must-not-run"\n')
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stdout, "")
        self.assertRegex(
            result.stderr,
            r"^kubernetes_reference_failure function=main line=\d+ exit_status=37\n$",
        )

    def test_function_failure_identifies_its_boundary_without_arguments(self) -> None:
        result = self.run_shell(
            "failing_probe() {\n"
            "  test 'synthetic-sensitive-argument' = 'different'\n"
            "}\n"
            "failing_probe\n"
            'printf "must-not-run"\n'
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertRegex(
            result.stderr,
            r"^kubernetes_reference_failure function=failing_probe line=\d+ exit_status=1\n$",
        )
        self.assertNotIn("synthetic-sensitive-argument", result.stderr)

    def test_command_substitution_reports_location_not_captured_values(self) -> None:
        result = self.run_shell(
            "lookup_pod() {\n"
            "  printf '%s' 'synthetic-sensitive-output'\n"
            "  return 44\n"
            "}\n"
            'pod="$(lookup_pod)"\n'
            'printf "must-not-run"\n'
        )
        self.assertEqual(result.returncode, 44)
        self.assertEqual(result.stdout, "")
        self.assertRegex(result.stderr, r"line=\d+ exit_status=44")
        self.assertNotIn("synthetic-sensitive-output", result.stderr)
        self.assertNotIn("lookup_pod)", result.stderr)

    def test_expected_conditional_failure_does_not_report_or_abort(self) -> None:
        result = self.run_shell(
            "probe() { return 9; }\n"
            "if probe; then exit 1; else printf 'expected'; fi\n"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "expected")
        self.assertEqual(result.stderr, "")

    def test_actual_cleanup_preserves_failure_without_extra_diagnostics(self) -> None:
        result = self.run_shell(
            shell_function("cleanup")
            + '\nCLUSTER_CREATED=0\nWORK_ROOT=""\n'
            + 'trap cleanup EXIT\nbash -c "exit 39"\n'
        )
        self.assertEqual(result.returncode, 39)
        self.assertEqual(result.stdout, "")
        self.assertRegex(
            result.stderr,
            r"^kubernetes_reference_failure function=main line=\d+ exit_status=39\n$",
        )


if __name__ == "__main__":
    unittest.main()
