from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
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

    def run_log_capture(
        self, responses: list[dict[str, object]],
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]], dict[str, str], str]:
        with tempfile.TemporaryDirectory(prefix="hormuz gateway capture ") as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            secrets = root / "secrets"
            artifacts.mkdir()
            secrets.mkdir()
            (secrets / "credential").write_text("synthetic-capture-secret", encoding="utf-8")
            (root / "responses.json").write_text(json.dumps(responses), encoding="utf-8")
            fake = root / "kubectl.py"
            fake.write_text(textwrap.dedent("""\
                import json
                import sys
                from pathlib import Path

                root = Path(sys.argv[1])
                args = sys.argv[2:]
                calls = root / "calls.jsonl"
                previous = calls.read_text().splitlines() if calls.exists() else []
                with calls.open("a") as stream:
                    stream.write(json.dumps(args) + "\\n")
                responses = json.loads((root / "responses.json").read_text())
                if len(previous) >= len(responses):
                    raise SystemExit("unexpected extra Kubernetes request")
                response = responses[len(previous)]
                command = response["command"]
                if command[:2] == ["get", "pods"]:
                    flags = ["--selector=app.kubernetes.io/instance=hormuz,app.kubernetes.io/component=gateway", "--output=name"]
                elif command[0] == "get":
                    flags = ["--output=name"]
                else:
                    flags = ["--all-containers=true", "--prefix=true", "--tail=-1"]
                if args != ["--namespace", "hormuz-system", *command, *flags]:
                    raise SystemExit("unexpected Kubernetes arguments")
                sys.stdout.write(response.get("stdout", ""))
                sys.stderr.write(response.get("stderr", ""))
                raise SystemExit(response.get("status", 0))
                """), encoding="utf-8")
            body = "\n".join((
                "umask 077",
                f"ROOT={shlex.quote(str(ROOT))}",
                f"ARTIFACT_ROOT={shlex.quote(str(artifacts))}",
                f"SECRET_ROOT={shlex.quote(str(secrets))}",
                f"python3() {{ {shlex.quote(sys.executable)} \"$@\"; }}",
                f"kubectl() {{ python3 {shlex.quote(str(fake))} {shlex.quote(str(root))} \"$@\"; }}",
                f"sleep() {{ printf '%s\\n' \"$1\" >>{shlex.quote(str(root / 'delays'))}; }}",
                shell_function("fail"),
                shell_function("classify_gateway_log_error"),
                shell_function("capture_gateway_logs"),
                "capture_gateway_logs synthetic-checkpoint",
                "printf 'capture-complete\\n'",
            ))
            result = self.run_shell(body)
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            captured = {path.name: path.read_text() for path in artifacts.iterdir()}
            for path in artifacts.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            delays = (root / "delays").read_text() if (root / "delays").exists() else ""
            return result, calls, captured, delays

    @staticmethod
    def pod_list(value: str, *, stderr: str = "", status: int = 0) -> dict[str, object]:
        return {"command": ["get", "pods"], "stdout": value, "stderr": stderr, "status": status}

    @staticmethod
    def pod_logs(pod: str, value: str, *, stderr: str = "", status: int = 0) -> dict[str, object]:
        return {"command": ["logs", f"pod/{pod}"], "stdout": value, "stderr": stderr, "status": status}

    @staticmethod
    def pod_observation(pod: str, *, present: bool = True) -> dict[str, object]:
        return {
            "command": ["get", f"pod/{pod}"],
            "stdout": f"pod/{pod}\n" if present else "",
            "stderr": "" if present else f'Error from server (NotFound): pods "{pod}" not found\n',
            "status": 0 if present else 1,
        }

    @staticmethod
    def pod_missing(pod: str) -> str:
        return f'Error from server (NotFound): pods "{pod}" not found\n'

    def test_log_capture_refreshes_pods_and_preserves_partial_logs(self) -> None:
        result, calls, captured, delays = self.run_log_capture([
            self.pod_list("pod/stable\npod/retiring\n"),
            self.pod_logs("stable", "stable first\n"),
            self.pod_logs("retiring", "retiring partial\n", stderr=self.pod_missing("retiring"), status=1),
            self.pod_list("pod/stable\npod/replacement\n"),
            self.pod_logs("stable", "stable refreshed\n"),
            self.pod_logs("replacement", "replacement final\n"),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "capture-complete\n")
        self.assertEqual(len(calls), 6)
        self.assertEqual(captured["gateway-synthetic-checkpoint.log"],
                         "stable first\nretiring partial\nstable refreshed\nreplacement final\n")
        self.assertEqual(captured["gateway-synthetic-checkpoint-1-retiring.stderr"], self.pod_missing("retiring"))
        self.assertNotEqual(captured["gateway-synthetic-checkpoint-pods-1.txt"],
                            captured["gateway-synthetic-checkpoint-pods-2.txt"])
        self.assertEqual(delays, "1\n")
        self.assertEqual(result.stderr, "gateway_log_capture_retry checkpoint=synthetic-checkpoint attempt=1\n")

    def test_log_capture_allows_only_three_refreshed_attempts(self) -> None:
        for succeeds in (False, True):
            with self.subTest(succeeds=succeeds):
                responses = []
                for attempt in range(1, 4):
                    pod = f"replica-{attempt}"
                    missing = not (succeeds and attempt == 3)
                    responses.extend([
                        self.pod_list(f"pod/{pod}\n"),
                        self.pod_logs(pod, f"partial-{attempt}\n",
                                      stderr=self.pod_missing(pod) if missing else "",
                                      status=1 if missing else 0),
                    ])
                result, calls, captured, delays = self.run_log_capture(responses)
                self.assertEqual(len(calls), 6)
                self.assertEqual(delays, "1\n1\n")
                self.assertEqual(captured["gateway-synthetic-checkpoint.log"], "partial-1\npartial-2\npartial-3\n")
                if succeeds:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("exhausted pod disappearance retries", result.stderr)

    def test_log_capture_retries_unknown_error_only_after_exact_pod_disappearance(self) -> None:
        warning = 'Defaulted container "gateway" out of: gateway, configuration-preflight (init)\n'
        result, calls, captured, delays = self.run_log_capture([
            self.pod_list("pod/retiring\n"),
            self.pod_logs("retiring", "retiring partial\n", stderr=warning, status=1),
            self.pod_observation("retiring", present=False),
            self.pod_list("pod/replacement\n"),
            self.pod_logs("replacement", "replacement final\n"),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 5)
        self.assertEqual(delays, "1\n")
        self.assertEqual(
            captured["gateway-synthetic-checkpoint.log"],
            "retiring partial\nreplacement final\n",
        )
        self.assertNotIn("gateway_log_capture_error", result.stderr)

    def test_log_capture_does_not_retry_unrelated_errors(self) -> None:
        failures = (
            (1, 'Error from server (Forbidden): pods "selected" is forbidden\n'),
            (1, 'Error from server (NotFound): namespaces "hormuz-system" not found\n'),
            (1, self.pod_missing("different-pod")),
            (1, self.pod_missing("selected") + "unrelated API failure\n"),
            (1, self.pod_missing("selected") + "\x00"),
            (1, self.pod_missing("selected") + "\n"),
            (1, "Unable to connect to the server: timeout\n"),
            (2, self.pod_missing("selected")),
        )
        for status, error in failures:
            with self.subTest(status=status, error=error):
                result, calls, captured, delays = self.run_log_capture([
                    self.pod_list("pod/selected\n"),
                    self.pod_logs("selected", "partial output\n", stderr=error, status=status),
                ])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), 2)
                self.assertEqual(delays, "")
                self.assertEqual(captured["gateway-synthetic-checkpoint.log"], "partial output\n")
                self.assertIn("gateway log capture failed", result.stderr)
                self.assertNotIn(error.strip(), result.stderr)

    def test_log_capture_reports_only_allowlisted_error_classes(self) -> None:
        sensitive = "synthetic-sensitive-pod /private/tmp/synthetic-sensitive-path"
        scenarios = (
            (1, self.pod_missing("different-pod") + sensitive, "not_found"),
            (1, f'Error from server (Forbidden): {sensitive}\n', "forbidden"),
            (1, f'Unable to connect to the server: timeout {sensitive}\n', "timeout"),
            (1, f'error: unable to upgrade connection: {sensitive}\n', "connection"),
            (1, f'unrecognized error: {sensitive}\n', "unknown"),
            (2, self.pod_missing("selected"), "not_found"),
        )
        for status, error, expected in scenarios:
            with self.subTest(status=status, expected=expected):
                responses = [
                    self.pod_list("pod/selected\n"),
                    self.pod_logs("selected", "partial output\n", stderr=error, status=status),
                ]
                if expected == "unknown" and status == 1:
                    responses.append(self.pod_observation("selected"))
                result, calls, _captured, delays = self.run_log_capture(responses)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(calls), len(responses))
                self.assertEqual(delays, "")
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    result.stderr,
                    f"gateway_log_capture_error checkpoint=synthetic-checkpoint "
                    f"class={expected} exit_status={status}\n"
                    f"Kubernetes reference proof failed: gateway log capture failed: "
                    f"synthetic-checkpoint exit_status={status}\n",
                )
                self.assertNotIn(sensitive, result.stderr)
                self.assertNotIn("selected", result.stderr)

    def test_log_capture_fails_closed_on_empty_or_invalid_selection(self) -> None:
        for selection in ("", "\n", "\x00", "deployment/selected\n", "pod/../../escape\n"):
            with self.subTest(selection=selection):
                result, calls, _captured, delays = self.run_log_capture([self.pod_list(selection)])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(len(calls), 1)
                self.assertEqual(delays, "")
                self.assertRegex(result.stderr, r"gateway pod selection (empty|invalid)")

    def test_log_capture_fails_when_refreshed_selection_is_empty(self) -> None:
        result, calls, captured, delays = self.run_log_capture([
            self.pod_list("pod/retiring\n"),
            self.pod_logs("retiring", "partial output\n", stderr=self.pod_missing("retiring"), status=1),
            self.pod_list(""),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(delays, "1\n")
        self.assertEqual(captured["gateway-synthetic-checkpoint.log"], "partial output\n")
        self.assertIn("gateway pod selection empty", result.stderr)

    def test_log_capture_does_not_retry_selection_failure(self) -> None:
        result, calls, _captured, delays = self.run_log_capture([
            self.pod_list("pod/selected\n", stderr=self.pod_missing("selected"), status=1),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(delays, "")
        self.assertIn("gateway pod selection failed", result.stderr)

    def test_log_capture_checks_last_pod_without_a_trailing_newline(self) -> None:
        result, calls, _captured, delays = self.run_log_capture([
            self.pod_list("pod/selected"), self.pod_logs("selected", "complete output\n"),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertEqual(delays, "")

    def test_log_capture_scans_partial_final_and_error_output(self) -> None:
        secret = "synthetic-capture-secret"
        scenarios = (
            [self.pod_list("pod/selected\n"),
             self.pod_logs("selected", secret, stderr=self.pod_missing("selected"), status=1)],
            [self.pod_list("pod/selected\n"),
             self.pod_logs("selected", "partial output\n", stderr=self.pod_missing("selected"), status=1),
             self.pod_list("pod/replacement\n"), self.pod_logs("replacement", secret)],
            [self.pod_list("pod/selected\n"),
             self.pod_logs("selected", "partial output\n", stderr=secret, status=1)],
            [self.pod_list(secret)],
            [self.pod_list("pod/selected\n", stderr=secret, status=1)],
        )
        for responses in scenarios:
            with self.subTest(responses=responses):
                result, calls, _captured, _delays = self.run_log_capture(responses)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), len(responses))
                self.assertIn("failed secret non-disclosure", result.stderr)
                self.assertNotIn("gateway_log_capture_error", result.stderr)
                self.assertNotIn(secret, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
