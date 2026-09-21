from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNERS = (
    ROOT / "tools/verify_helm_profile.sh",
    ROOT / "tools/verify_postgres_ha_reference.sh",
    ROOT / "tools/verify_disaster_recovery_reference.sh",
)
PAYLOAD = b"pinned kind release asset\n"


def shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{\n")
    return source[start : source.index("\n}\n", start) + 3]


class GitHubReleaseAssetDownloadTests(unittest.TestCase):
    def run_download(
        self, runner: Path, *, mode: str, expected: str, authenticated: bool = True
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]], str]:
        with tempfile.TemporaryDirectory(prefix="hormuz release asset ") as temporary:
            root = Path(temporary)
            fake_gh = root / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "calls = Path(os.environ['TEST_GH_CALLS'])\n"
                "previous = calls.read_text().splitlines() if calls.exists() else []\n"
                "with calls.open('a') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if os.environ['TEST_GH_MODE'] == 'fail' or "
                "(os.environ['TEST_GH_MODE'] == 'retry' and not previous):\n"
                "    raise SystemExit(1)\n"
                "output = Path(sys.argv[sys.argv.index('--output') + 1])\n"
                "output.write_bytes(b'pinned kind release asset\\n')\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o700)
            fake_curl = root / "curl"
            fake_curl.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "from pathlib import Path\n"
                "output = Path(sys.argv[sys.argv.index('--output') + 1])\n"
                "output.write_bytes(b'pinned kind release asset\\n')\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o700)
            fake_sha256sum = root / "sha256sum"
            fake_sha256sum.write_text(
                "#!/usr/bin/env python3\n"
                "import hashlib, sys\n"
                "from pathlib import Path\n"
                "assert sys.argv[1:] == ['--check', '--status']\n"
                "expected, separator, filename = sys.stdin.read().rstrip('\\n').partition('  ')\n"
                "assert separator\n"
                "actual = hashlib.sha256(Path(filename).read_bytes()).hexdigest()\n"
                "raise SystemExit(actual != expected)\n",
                encoding="utf-8",
            )
            fake_sha256sum.chmod(0o700)
            output = root / "kind binary"
            source = runner.read_text(encoding="utf-8")
            script = "\n".join(
                (
                    "set -Eeuo pipefail",
                    "fail() { printf 'failed: %s\\n' \"$1\" >&2; exit 73; }",
                    "sleep() { :; }",
                    shell_function(source, "download_and_verify"),
                    shell_function(source, "download_github_release_and_verify"),
                    "download_github_release_and_verify kubernetes-sigs/kind v0.32.0 "
                    f"kind-linux-amd64 {shlex.quote(str(output))} {expected}",
                )
            )
            calls_path = root / "calls.jsonl"
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "TEST_GH_CALLS": str(calls_path),
                    "TEST_GH_MODE": mode,
                    "GH_TOKEN": "mock-token" if authenticated else "",
                    "GITHUB_TOKEN": "",
                },
            )
            calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
            content = output.read_text() if output.exists() else ""
            return result, calls, content

    def test_pinned_asset_succeeds_after_transient_api_failure(self) -> None:
        digest = hashlib.sha256(PAYLOAD).hexdigest()
        for runner in RUNNERS:
            with self.subTest(runner=runner.name):
                result, calls, content = self.run_download(
                    runner, mode="retry", expected=digest
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(content.encode(), PAYLOAD)
                self.assertEqual(len(calls), 2)
                for args in calls:
                    self.assertEqual(
                        args[:6],
                        [
                            "release",
                            "download",
                            "v0.32.0",
                            "--repo",
                            "kubernetes-sigs/kind",
                            "--pattern",
                        ],
                    )
                    self.assertEqual(args[6], "kind-linux-amd64")
                    self.assertEqual(args[7], "--output")

    def test_checksum_mismatch_is_not_retried_or_accepted(self) -> None:
        wrong_digest = hashlib.sha256(b"different release asset").hexdigest()
        for runner in RUNNERS:
            with self.subTest(runner=runner.name):
                result, calls, _ = self.run_download(
                    runner, mode="success", expected=wrong_digest
                )
                self.assertEqual(result.returncode, 73)
                self.assertEqual(len(calls), 1)
                self.assertIn("download checksum mismatch", result.stderr)

    def test_api_failure_stops_after_bounded_retries(self) -> None:
        digest = hashlib.sha256(PAYLOAD).hexdigest()
        for runner in RUNNERS:
            with self.subTest(runner=runner.name):
                result, calls, content = self.run_download(
                    runner, mode="fail", expected=digest
                )
                self.assertEqual(result.returncode, 73)
                self.assertEqual(len(calls), 3)
                self.assertEqual(content, "")
                self.assertIn("GitHub release asset download failed", result.stderr)

    def test_clean_machine_falls_back_to_public_curl_with_checksum(self) -> None:
        digest = hashlib.sha256(PAYLOAD).hexdigest()
        wrong_digest = hashlib.sha256(b"different release asset").hexdigest()
        for runner in RUNNERS:
            with self.subTest(runner=runner.name):
                result, calls, content = self.run_download(
                    runner, mode="fail", expected=digest, authenticated=False
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, [])
                self.assertEqual(content.encode(), PAYLOAD)
                rejected, calls, _ = self.run_download(
                    runner, mode="fail", expected=wrong_digest, authenticated=False
                )
                self.assertEqual(rejected.returncode, 73)
                self.assertEqual(calls, [])
                self.assertIn("download checksum mismatch", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
