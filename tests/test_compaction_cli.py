from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from hormuz.cli import main
from hormuz.compaction_formats import restore_text
from hormuz.compaction_runtime import ContextPreferenceStore


COUNTERS = {"cl100k_base": len, "o200k_base": len}


class ContextCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = str(uuid.uuid4())

    def _write_profile(self, state: Path) -> None:
        state.mkdir(mode=0o700)
        profile = {
            "id": self.profile,
            "gateway": "https://gateway.example.test",
            "organization": "org-a",
            "issuer": None,
            "client": "codex",
            "model": "approved",
            "allowLoopbackHTTP": False,
        }
        path = state / "profile.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        path.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_settings_and_status_do_not_load_gateway_config(self) -> None:
        state = self.root / "state"
        self._write_profile(state)
        with mock.patch(
            "hormuz.cli.GatewayConfig.load",
            side_effect=AssertionError("gateway config was opened"),
        ):
            code, output, error = self._run(
                "--config", "/missing/hormuz.json", "context", "settings",
                "--profile", self.profile, "--state-directory", str(state), "--enabled", "off",
            )
            self.assertEqual((code, error), (0, ""))
            self.assertIn("setting=off", output)
            code, output, error = self._run(
                "--config", "/missing/hormuz.json", "context", "status",
                "--profile", self.profile, "--state-directory", str(state),
            )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output, "context_optimization setting=off status=off\n")

    def test_settings_reject_unknown_saved_profile(self) -> None:
        state = self.root / "state"
        self._write_profile(state)
        code, _output, error = self._run(
            "context", "settings", "--profile", str(uuid.uuid4()),
            "--state-directory", str(state), "--enabled", "on",
        )
        self.assertEqual(code, 2)
        self.assertIn("profile_invalid", error)
        self.assertEqual(list(state.glob("context-optimization-*.json")), [])

    def test_enabled_status_reports_gateway_readiness_without_gateway_config(self) -> None:
        state = self.root / "status-state"
        self._write_profile(state)
        ContextPreferenceStore(state, self.profile).save(True)
        with (
            mock.patch("hormuz.commands.context.supported_client_executable", return_value="/bin/codex"),
            mock.patch("hormuz.commands.context.load_token_counters", return_value=COUNTERS),
            mock.patch("hormuz.commands.context.probe_gateway_capability", return_value=False),
            mock.patch(
                "hormuz.cli.GatewayConfig.load",
                side_effect=AssertionError("gateway config was opened"),
            ),
        ):
            code, output, error = self._run(
                "--config", "/missing/hormuz.json", "context", "status",
                "--profile", self.profile, "--state-directory", str(state),
            )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(
            output,
            "context_optimization setting=on status=gateway_incompatible\n",
        )

    def test_disabled_compaction_preserves_exact_input_without_tokenizer(self) -> None:
        request = self.root / "request.json"
        selection = self.root / "selection.json"
        output = self.root / "output.json"
        metadata = self.root / "metadata.json"
        original = b'{ "model" : "approved", "input" : [] }\n'
        request.write_bytes(original)
        selection.write_text('{"enabled":false}', encoding="utf-8")
        with mock.patch(
            "hormuz.commands.context.load_token_counters",
            side_effect=AssertionError("tokenizer was loaded"),
        ):
            code, _stdout, stderr = self._run(
                "context", "compact", "--protocol", "responses", "--input", str(request),
                "--selection", str(selection), "--output", str(output), "--metadata", str(metadata),
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(metadata.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(metadata.read_text())["reason"], "disabled")

    def test_enabled_compaction_writes_lossless_request_and_content_free_metadata(self) -> None:
        request = self.root / "request.json"
        selection = self.root / "selection.json"
        output = self.root / "output.json"
        metadata = self.root / "metadata.json"
        rows = [{"id": index, "status": "ok", "category": "same"} for index in range(120)]
        payload = {"model": "approved", "input": [
            {"type": "function_call", "call_id": "records", "name": "list_records", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "records", "output": json.dumps(rows, separators=(",", ":"))},
        ]}
        request.write_text(json.dumps(payload), encoding="utf-8")
        selection.write_text(json.dumps({
            "enabled": True, "version": "structural-v1",
            "selections": [{"result_id": "records", "format": "json_table"}],
        }), encoding="utf-8")
        with mock.patch("hormuz.commands.context.load_token_counters", return_value=COUNTERS):
            code, stdout, stderr = self._run(
                "context", "compact", "--protocol", "responses", "--input", str(request),
                "--selection", str(selection), "--output", str(output), "--metadata", str(metadata),
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("changed_blocks=1", stdout)
        changed = json.loads(output.read_text())
        self.assertEqual(restore_text(changed["input"][1]["output"]), payload["input"][1]["output"])
        evidence = json.loads(metadata.read_text())
        self.assertEqual(evidence["action"], "compacted")
        self.assertNotIn("records", metadata.read_text())
        self.assertNotIn("same", metadata.read_text())

    def test_invalid_selection_collision_and_missing_resources_write_nothing(self) -> None:
        request = self.root / "request.json"
        request.write_text('{"input":[]}', encoding="utf-8")
        duplicate = self.root / "duplicate.json"
        duplicate.write_text(
            '{"enabled":true,"selections":[{"result_id":"x","format":"line_runs"},'
            '{"result_id":"x","format":"path_list"}]}', encoding="utf-8",
        )
        metadata = self.root / "metadata.json"
        code, _stdout, stderr = self._run(
            "context", "compact", "--protocol", "responses", "--input", str(request),
            "--selection", str(duplicate), "--output", str(request), "--metadata", str(metadata),
        )
        self.assertEqual(code, 2)
        self.assertIn("path_collision", stderr)

        output = self.root / "output.json"
        empty_cache = self.root / "empty-cache"
        empty_cache.mkdir()
        code, _stdout, stderr = self._run(
            "context", "compact", "--protocol", "responses", "--input", str(request),
            "--selection", str(duplicate), "--output", str(output), "--metadata", str(metadata),
            "--tokenizer-cache", str(empty_cache),
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid_selection", stderr)
        self.assertFalse(output.exists())
        self.assertFalse(metadata.exists())

        duplicate.write_text('{"enabled":true,"selections":[]}', encoding="utf-8")
        code, _stdout, stderr = self._run(
            "context", "compact", "--protocol", "responses", "--input", str(request),
            "--selection", str(duplicate), "--output", str(output), "--metadata", str(metadata),
            "--tokenizer-cache", str(empty_cache),
        )
        self.assertEqual(code, 3)
        self.assertIn("resources_unavailable", stderr)
        self.assertFalse(output.exists())
        self.assertFalse(metadata.exists())

    def test_failed_second_output_removes_only_new_first_output(self) -> None:
        request = self.root / "request.json"
        selection = self.root / "selection.json"
        output = self.root / "output.json"
        request.write_text('{"input":[]}', encoding="utf-8")
        selection.write_text('{"enabled":false}', encoding="utf-8")
        code, _stdout, stderr = self._run(
            "context", "compact", "--protocol", "responses", "--input", str(request),
            "--selection", str(selection), "--output", str(output),
            "--metadata", str(self.root / "missing" / "metadata.json"),
        )
        self.assertEqual(code, 1)
        self.assertIn("output_write_failed", stderr)
        self.assertFalse(output.exists())

    def test_resource_install_rejects_symlinked_directory_before_download(self) -> None:
        actual = self.root / "actual-cache"
        actual.mkdir(mode=0o700)
        linked = self.root / "linked-cache"
        linked.symlink_to(actual, target_is_directory=True)

        with mock.patch(
            "hormuz.commands.context.urllib.request.urlopen",
            side_effect=AssertionError("download attempted"),
        ):
            code, stdout, stderr = self._run(
                "context", "resources", "install", "--directory", str(linked)
            )

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("resource_directory_unsafe", stderr)
        self.assertEqual(list(actual.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
