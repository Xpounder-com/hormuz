from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tools.verify_repository_governance import (
    RepositoryGovernanceError,
    validate_repository_governance,
)


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/ci.yml"
LOGIN = "Configure optional Docker Hub read authentication"
CLEANUP = "Remove isolated Docker Hub read credentials"
DOCKER_JOBS = {
    "postgres-compatibility", "postgres-backup-restore", "oci-reference-runtime",
    "render-https-preflight", "render-authentication-staging", "compose-reference",
    "kubernetes-reference", "postgres-ha-reference", "disaster-recovery-reference",
    "oci-supply-chain", "oci-reproducibility",
}


def jobs(text: str) -> dict[str, str]:
    starts = list(re.finditer(r"^  ([a-z][a-z0-9-]*):\n", text, re.MULTILINE))
    return {
        match[1]: text[match.start():starts[index + 1].start() if index + 1 < len(starts) else len(text)]
        for index, match in enumerate(starts)
    }


def step(job: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = job.index(marker)
    following = job.find("      - name:", start + len(marker))
    return job[start:following if following >= 0 else len(job)]


def script(value: str) -> str:
    return textwrap.dedent(value.split("        run: |\n", 1)[1]).rstrip() + "\n"


class DockerHubAuthBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runner = self.root / "runner"
        self.runner.mkdir()
        self.config = self.runner / "hormuz-dockerhub-read-auth"
        self.capture = self.root / "docker-call.json"
        self.timeout_capture = self.root / "timeout-call.json"
        job = jobs(WORKFLOW.read_text())["oci-reference-runtime"]
        self.login = script(step(job, LOGIN))
        self.cleanup = script(step(job, CLEANUP))
        self._executable("docker", '''
import json, os, pathlib, sys
token = sys.stdin.read()
capture = pathlib.Path(os.environ["MOCK_DOCKER_CAPTURE"])
calls = json.loads(capture.read_text()) if capture.exists() else []
calls.append({
    "argv": sys.argv[1:], "stdin": token,
    "token_env": os.environ.get("DOCKERHUB_READ_TOKEN"),
    "username_env": os.environ.get("DOCKERHUB_USERNAME"),
})
capture.write_text(json.dumps(calls))
pathlib.Path(os.environ["DOCKER_CONFIG"], "config.json").write_text("synthetic auth fixture")
print("synthetic suppressed output: " + token)
print("synthetic suppressed error: " + token, file=sys.stderr)
sys.exit(int(os.environ["MOCK_DOCKER_STATUS"]))
''')
        self._executable("timeout", '''
import json, os, pathlib, subprocess, sys
capture = pathlib.Path(os.environ["MOCK_TIMEOUT_CAPTURE"])
calls = json.loads(capture.read_text()) if capture.exists() else []
calls.append(sys.argv[1:])
capture.write_text(json.dumps(calls))
if os.environ["MOCK_TIMEOUT_STATUS"] == "124":
    sys.exit(124)
sys.exit(subprocess.run(sys.argv[3:], timeout=3).returncode)
''')

    def _executable(self, name: str, source: str) -> None:
        path = self.root / name
        path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(source))
        path.chmod(0o700)

    def _run(self, username: str = "", token: str = "", *, fail: bool = False, timeout: bool = False) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": f"{self.root}:/usr/bin:/bin",
            "RUNNER_TEMP": str(self.runner), "DOCKER_CONFIG": str(self.config),
            "DOCKERHUB_USERNAME": username, "DOCKERHUB_READ_TOKEN": token,
            "MOCK_DOCKER_CAPTURE": str(self.capture),
            "MOCK_TIMEOUT_CAPTURE": str(self.timeout_capture),
            "MOCK_DOCKER_STATUS": "7" if fail else "0",
            "MOCK_TIMEOUT_STATUS": "124" if timeout else "0",
        }
        return subprocess.run(
            ["/bin/bash", "-x", "-e", "-o", "pipefail", "-c", self.login],
            env=environment, text=True, capture_output=True, timeout=5,
        )

    def _cleanup(self) -> None:
        other = self.runner / "unrelated"
        other.mkdir(exist_ok=True)
        metadata = self.config / "buildx" / "owned-builder"
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text("retain for the existing action's owned-builder cleanup")
        result = subprocess.run(
            ["/bin/bash", "-e", "-o", "pipefail", "-c", self.cleanup],
            env={"PATH": "/usr/bin:/bin", "RUNNER_TEMP": str(self.runner)},
            text=True, capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.config / "config.json").exists())
        self.assertTrue(metadata.exists())
        self.assertTrue(other.exists())

    def test_complete_credentials_use_one_bounded_login_and_only_stdin_for_token(self) -> None:
        token = "synthetic-public-read-token"
        result = self._run("synthetic-user", token)
        self.assertEqual(result.returncode, 0)
        calls = json.loads(self.capture.read_text())
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["argv"], ["login", "--username", "synthetic-user", "--password-stdin", "docker.io"])
        self.assertEqual(call["stdin"], token)
        self.assertIsNone(call["token_env"])
        self.assertIsNone(call["username_env"])
        self.assertNotIn(token, result.stdout + result.stderr)
        bounds = json.loads(self.timeout_capture.read_text())
        self.assertEqual(len(bounds), 1)
        bound = bounds[0]
        self.assertEqual(bound[:3], ["--kill-after=5s", "30s", "docker"])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.config / "config.json").stat().st_mode & 0o777, 0o600)
        self._cleanup()

    def test_absent_credentials_keep_anonymous_fallback_without_docker_or_timeout(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("using anonymous pulls", result.stdout)
        self.assertFalse(self.capture.exists())
        self.assertFalse(self.timeout_capture.exists())
        self._cleanup()

    def test_partial_pair_fails_before_auth_and_does_not_print_values(self) -> None:
        for username, token in [("synthetic-user", ""), ("", "synthetic-secret-only")]:
            with self.subTest(username_present=bool(username), token_present=bool(token)):
                result = self._run(username, token)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Configure both", result.stdout)
                self.assertFalse(self.capture.exists())
                self.assertFalse(self.timeout_capture.exists())
                if token:
                    self.assertNotIn(token, result.stdout + result.stderr)
                self._cleanup()

    def test_login_failure_is_not_retried_and_suppresses_output_before_cleanup(self) -> None:
        token = "synthetic-failed-secret"
        result = self._run("synthetic-user", token, fail=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("failed or timed out", result.stdout)
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertTrue(self.capture.exists())
        self.assertEqual(len(json.loads(self.capture.read_text())), 1)
        self._cleanup()

    def test_timeout_fails_closed_and_cleanup_removes_created_config(self) -> None:
        result = self._run("synthetic-user", "synthetic-timeout-secret", timeout=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.capture.exists())
        self.assertTrue(self.timeout_capture.exists())
        self._cleanup()


class DockerHubAuthGovernanceTests(unittest.TestCase):
    def _mutate(self, change) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / ".github", root / ".github")
            (root / "tools").mkdir()
            shutil.copy2(ROOT / "tools/v1_candidate.py", root / "tools/v1_candidate.py")
            workflow = root / ".github/workflows/ci.yml"
            before = workflow.read_text()
            after = change(before)
            self.assertNotEqual(before, after)
            workflow.write_text(after)
            with self.assertRaises(RepositoryGovernanceError):
                validate_repository_governance(root)

    def test_all_docker_jobs_have_identical_auth_and_cleanup_with_two_startup_services(self) -> None:
        blocks = jobs(WORKFLOW.read_text())
        self.assertEqual({name for name, value in blocks.items() if LOGIN in value}, DOCKER_JOBS)
        self.assertEqual(len({script(step(blocks[name], LOGIN)) for name in DOCKER_JOBS}), 1)
        self.assertEqual(len({script(step(blocks[name], CLEANUP)) for name in DOCKER_JOBS}), 1)
        self.assertEqual({name for name, value in blocks.items() if "        credentials:\n" in value}, {"postgres-compatibility", "kubernetes-reference"})
        self.assertEqual(validate_repository_governance(ROOT)["status"], "passed")

    def test_fork_guard_repo_guard_or_service_pair_guard_cannot_be_removed(self) -> None:
        for before, after in [
            ("github.event.pull_request.head.repo.full_name == github.repository", "true"),
            ("github.event.pull_request.head.repo.full_name", "github.event.pull_request.base.repo.full_name"),
            ("github.repository == 'Xpounder-com/hormuz'", "true"),
            (" && secrets.DOCKERHUB_READ_TOKEN != ''", ""),
        ]:
            with self.subTest(guard=before):
                self._mutate(lambda text: text.replace(before, after, 1))

    def test_token_argv_output_extra_commands_and_unbounded_login_are_rejected(self) -> None:
        for before, after in [
            ("--password-stdin docker.io", '--password "$DOCKERHUB_READ_TOKEN" docker.io'),
            ("          set +x\n", "          set -x\n"),
            ("30s docker login", "90s docker login"),
            ('          umask 077\n', '          echo "$DOCKERHUB_READ_TOKEN"\n          umask 077\n'),
            ("          PATH: /usr/bin:/bin", "          PATH: .:/usr/bin:/bin"),
        ]:
            with self.subTest(boundary=before):
                self._mutate(lambda text: text.replace(before, after, 1))

    def test_cleanup_cannot_be_skipped_or_delete_an_arbitrary_directory(self) -> None:
        for before, after in [
            ("        if: ${{ always() }}\n        shell: bash\n        run: |\n          set -euo pipefail\n          rm -f", "        if: ${{ success() }}\n        shell: bash\n        run: |\n          set -euo pipefail\n          rm -f"),
            ('rm -f -- "${RUNNER_TEMP:?}/hormuz-dockerhub-read-auth/config.json"', 'rm -rf -- "$DOCKER_CONFIG"'),
            ("DOCKER_CONFIG: ${{ runner.temp }}/hormuz-dockerhub-read-auth", "DOCKER_CONFIG: ${{ github.workspace }}/auth"),
        ]:
            with self.subTest(boundary=before):
                self._mutate(lambda text: text.replace(before, after, 1))

    def test_known_credentials_cannot_move_to_a_build_step_or_gain_another_secret(self) -> None:
        self._mutate(lambda text: text.replace("DOCKERHUB_READ_TOKEN: ${{ github.repository", "OTHER_TOKEN: ${{ github.repository", 1))
        self._mutate(lambda text: text.replace("          umask 077\n", "          umask 077\n          : '${{ secrets.OPENAI_API_KEY }}'\n", 1))

    def test_registry_token_cannot_be_added_to_a_service_environment(self) -> None:
        def change(text: str) -> str:
            expression = re.search(r"^          DOCKERHUB_READ_TOKEN: (.+)$", text, re.MULTILINE)[1]
            marker = "          POSTGRES_DB: hormuz_test\n"
            return text.replace(marker, marker + "          DOCKERHUB_READ_TOKEN: " + expression + "\n", 1)
        self._mutate(change)

    def test_repository_helper_cannot_execute_before_login(self) -> None:
        marker = f"      - name: {LOGIN}\n"
        self._mutate(lambda text: text.replace(marker,
            "      - name: Untrusted helper before auth\n        run: ./tools/verify_oci_reference.sh\n" + marker, 1))


if __name__ == "__main__":
    unittest.main()
