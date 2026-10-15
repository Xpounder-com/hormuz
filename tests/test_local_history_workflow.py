"""Local history through the public CLI and connected coding-client workflow."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tests import test_execution_destination_workflow as destination_workflow


class LocalHistoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = destination_workflow.ExecutionDestinationWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root.resolve()
        self.fixture.root = self.root
        self.config_path = self.root / "gateway.json"
        self.history_home = self.root / "history"
        self.fixture.environment["HORMUZ_HISTORY_HOME"] = str(self.history_home)
        self.environment = {**os.environ, **self.fixture.environment}
        fixture_path = Path(__file__).parent / "fixtures" / "local-history-v1.json"
        self.compatibility = json.loads(fixture_path.read_text())

    def history(self, *arguments, expected=0, clock=None):
        command = [sys.executable, "-m", "hormuz"]
        if clock is not None:
            command = [sys.executable, "-c",
                       "import sys,time; time.time=lambda:float(sys.argv[1]); "
                       "from hormuz.cli import main; raise SystemExit(main(sys.argv[2:]))", str(clock)]
        result = subprocess.run(
            [*command, "--config", str(self.config_path), "history", *arguments],
            env=self.environment, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        value = json.loads(result.stdout)
        self.assertIn(value["schema_id"], self.compatibility["schemas"])
        self.assertEqual(value["schema_version"], self.compatibility["schema_version"])
        schema = "error" if expected else {
            "enable": "status", "disable": "status", "show": "review", "export": "export-result",
        }.get(arguments[0], arguments[0])
        self.assertEqual(value["schema_id"], "hormuz.local-history-" + schema)
        return value

    def test_history_requires_opt_in_without_credentials_or_running_router(self):
        status = self.history("status")
        self.assertEqual(status["version"], 1)
        self.assertFalse(status["enabled"])
        self.assertFalse(self.history_home.exists())

        self.config_path.write_text(json.dumps({"upstreams": {}, "model_routes": {},
                                                "identities": [{"actor_id": "developer"}]}))
        enabled = self.history("enable")
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["actor_id"], "developer")
        self.assertEqual(enabled["retention_days"], 30)
        self.assertTrue(enabled["encrypted"])
        self.assertEqual(enabled["capture_mode"], self.compatibility["capture_mode"])
        self.config_path.unlink()
        self.assertTrue(self.history("status")["enabled"])
        self.assertFalse(self.history("disable")["enabled"])
        self.assertEqual(self.history("list")["entries"], [])

    def test_user_reviews_exports_and_deletes_only_opted_in_local_history(self):
        prompt = "HISTORY_UNIQUE_PROMPT_673159"
        answer = "HISTORY_UNIQUE_ANSWER_673159"
        with destination_workflow.provider(answer) as (endpoint, received):
            value = self.fixture.configuration(endpoint, endpoint)
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(submit()["status"], 200)
                self.assertFalse(self.history_home.exists())
                self.history("enable")
                self.assertEqual(submit(request={"input": prompt})["status"], 200)
                listing = self.history("list")
                self.assertEqual(len(listing["entries"]), 1)
                self.assertNotIn(prompt, json.dumps(listing))
                self.assertNotIn(answer, json.dumps(listing))
                entry_id = listing["entries"][0]["id"]
                entry = self.history("show", entry_id)["entry"]
                self.assertEqual(set(entry), set(self.compatibility["entry_fields"]))
                self.assertIn(entry["schema_id"], self.compatibility["schemas"])
                self.assertEqual(entry["schema_version"], self.compatibility["schema_version"])
                self.assertIn(entry["outcome"], self.compatibility["outcomes"])
                self.assertIn(entry["request_content_status"], self.compatibility["content_statuses"])
                self.assertIn(entry["response_content_status"], self.compatibility["content_statuses"])
                self.assertIn(prompt, json.dumps(entry["request_text"]))
                self.assertEqual(entry["response_text"], answer)
                self.assertEqual(entry["request_content_status"], "captured")
                self.assertEqual(entry["response_content_status"], "captured")
                self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (4, 2))
                for path in self.history_home.rglob("*"):
                    if path.is_file():
                        ciphertext = path.read_bytes()
                        self.assertNotIn(prompt.encode(), ciphertext)
                        self.assertNotIn(answer.encode(), ciphertext)

                report = self.history("insights")
                self.assertEqual(report["coverage"], "retained_opt_in_history_only")
                self.assertEqual(report["totals"]["requests"], 1)
                self.assertEqual(report["totals"]["unknown_cost_requests"], 1)
                self.assertIsNone(report["totals"]["estimated_cost_microusd"])
                self.assertEqual(report["local_infrastructure_cost"], "unknown")
                self.assertNotIn(prompt, json.dumps(report))

                exported = self.root / "review.json"
                result = self.history("export", "--output", str(exported), "--id", entry_id)
                self.assertEqual(result["exported"], 1)
                artifact = exported.read_bytes()
                exported_value = json.loads(artifact)
                self.assertEqual(exported_value["schema_id"], "hormuz.local-history-export")
                self.assertEqual(exported_value["schema_version"], self.compatibility["schema_version"])
                self.assertEqual(set(exported_value["entries"][0]), set(self.compatibility["entry_fields"]))
                self.assertEqual(exported_value["entries"][0]["response_text"], answer)
                self.assertEqual(exported.stat().st_mode & 0o777, 0o600)
                self.assertIn("error", self.history("export", "--output", str(exported), expected=2))
                self.assertEqual(exported.read_bytes(), artifact)
                self.assertEqual(self.history("delete", entry_id)["deleted"], 1)
                self.assertEqual(self.history("list")["entries"], [])
                self.assertIn("error", self.history("show", entry_id, expected=2))

                self.assertEqual(submit(request={"input": prompt})["status"], 200)
                self.assertEqual(self.history("delete", "--all")["deleted"], 1)
                self.assertEqual(self.history("insights")["totals"]["requests"], 0)
                self.history("disable")
                self.assertEqual(submit()["status"], 200)
                self.assertEqual(self.history("list")["entries"], [])
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(submit()["status"], 200)
                self.assertFalse(self.history("status")["enabled"])
                self.assertEqual(self.history("list")["entries"], [])
            self.assertEqual(len(received), 5)

    def test_interrupted_ciphertext_write_does_not_block_review_or_deletion(self):
        with destination_workflow.provider("RECOVERABLE_HISTORY") as (endpoint, _received):
            value = self.fixture.configuration(endpoint, endpoint)
            with self.fixture.connected_client(value) as submit:
                profile = Path(self.history("enable")["storage_path"])
                self.assertEqual(submit()["status"], 200)
                entry_id = self.history("list")["entries"][0]["id"]
            saved = next(path for path in profile.rglob("*.enc") if path.stem == entry_id)
            interrupted = saved.parent / ".history-interrupted"
            interrupted.write_bytes(saved.read_bytes())
            interrupted.chmod(0o600)
            self.assertEqual(self.history("list")["entries"][0]["id"], entry_id)
            self.assertFalse(interrupted.exists())
            self.assertEqual(self.history("delete", "--all")["deleted"], 1)
            self.assertEqual(self.history("list")["entries"], [])

    def test_opt_in_requires_unambiguous_actor_and_strict_configuration_input(self):
        self.config_path.write_text('{"identities":[{"actor_id":"developer"}],'
                                    '"identities":[{"actor_id":"other"}]}')
        failure = self.history("enable", expected=2)
        self.assertEqual(failure["schema_id"], "hormuz.local-history-error")
        self.assertEqual(failure["schema_version"], 1)
        self.assertEqual(failure["error"]["code"], "history_configuration_invalid")
        self.assertFalse(self.history_home.exists())
        self.config_path.write_text(json.dumps({"upstreams": {}, "model_routes": {}, "identities": [
            {"actor_id": "developer"}, {"actor_id": "other"},
        ]}))
        self.assertEqual(self.history("enable", expected=2)["error"]["code"], "history_actor_required")
        self.assertEqual(self.history("enable", "--actor", "missing", expected=2)["error"]["code"],
                         "history_actor_unknown")
        self.assertFalse(self.history_home.exists())
        self.assertEqual(self.history("enable", "--actor", "developer")["actor_id"], "developer")

    def test_retention_cleanup_removes_expired_content_from_review_and_export(self):
        with destination_workflow.provider("EXPIRING_HISTORY") as (endpoint, _received):
            value = self.fixture.configuration(endpoint, endpoint)
            with self.fixture.connected_client(value) as submit:
                self.history("enable")
                self.assertEqual(submit()["status"], 200)
                entry_id = self.history("list")["entries"][0]["id"]
            future = time.time() + 31 * 86400
            failure = self.history("show", entry_id, expected=2, clock=future)
            self.assertEqual(failure["error"]["code"], "local_history_entry_not_found")
            self.assertEqual(self.history("list")["entries"], [])
            output = self.root / "expired-export.json"
            self.assertEqual(self.history("export", "--output", str(output))["exported"], 0)
            self.assertEqual(json.loads(output.read_text())["entries"], [])
            self.assertEqual(self.history("insights")["totals"]["requests"], 0)

    def test_unsafe_storage_links_and_permissions_fail_closed(self):
        self.config_path.write_text(json.dumps({"upstreams": {}, "model_routes": {},
                                                "identities": [{"actor_id": "developer"}]}))
        unrelated = self.root / "unrelated"
        unrelated.mkdir(mode=0o700)
        self.history_home.symlink_to(unrelated, target_is_directory=True)
        failure = self.history("enable", expected=2)
        self.assertEqual(failure["error"]["code"], "local_history_directory_unsafe")
        self.assertEqual(list(unrelated.iterdir()), [])
        self.history_home.unlink()
        profile = Path(self.history("enable")["storage_path"])
        profile.chmod(0o755)
        try:
            self.assertEqual(self.history("status", expected=2)["error"]["code"],
                             "local_history_directory_unsafe")
        finally:
            profile.chmod(0o700)
        self.assertFalse(self.history("disable")["enabled"])

    def test_unsafe_encrypted_artifact_and_crash_link_do_not_expose_other_files(self):
        with destination_workflow.provider("PRIVATE_HISTORY") as (endpoint, _received):
            value = self.fixture.configuration(endpoint, endpoint)
            with self.fixture.connected_client(value) as submit:
                profile = Path(self.history("enable")["storage_path"])
                self.assertEqual(submit()["status"], 200)
                entry_id = self.history("list")["entries"][0]["id"]
            saved = next(path for path in profile.rglob("*.enc") if path.stem == entry_id)
            saved.chmod(0o644)
            try:
                self.assertEqual(self.history("show", entry_id, expected=2)["error"]["code"],
                                 "local_history_file_unsafe")
            finally:
                saved.chmod(0o600)
            unrelated = self.root / "unrelated-secret.txt"
            unrelated.write_text("DO_NOT_EXPOSE_OR_DELETE_884727")
            unrelated.chmod(0o600)
            interrupted = saved.parent / ".history-unsafe-link"
            interrupted.symlink_to(unrelated)
            failure = self.history("list", expected=2)
            self.assertNotIn("DO_NOT_EXPOSE_OR_DELETE", json.dumps(failure))
            self.assertTrue(interrupted.is_symlink())
            self.assertEqual(unrelated.read_text(), "DO_NOT_EXPOSE_OR_DELETE_884727")
            interrupted.unlink()
            self.assertEqual(self.history("delete", "--all")["deleted"], 1)

    def test_authenticated_unsupported_or_malformed_durable_versions_fail_closed(self):
        with destination_workflow.provider("VERSIONED_HISTORY") as (endpoint, _received):
            value = self.fixture.configuration(endpoint, endpoint)
            with self.fixture.connected_client(value) as submit:
                profile = Path(self.history("enable")["storage_path"])
                self.assertEqual(submit()["status"], 200)
                entry_id = self.history("list")["entries"][0]["id"]
                entry = self.history("show", entry_id)["entry"]

            # Independent compatibility fixtures use only the documented file
            # envelope and standard AES-GCM, never storage implementation APIs.
            envelope = self.compatibility["encryption_envelope"]
            cipher = AESGCM((profile / envelope["key_file"]).read_bytes())
            header = envelope["header_utf8"].encode("utf-8")
            settings_path = profile / envelope["settings_file"]
            original_settings = settings_path.read_bytes()
            self.assertTrue(original_settings.startswith(header))
            nonce_end = len(header) + envelope["nonce_bytes"]
            settings_aad = envelope["settings_aad_utf8"].encode("utf-8")
            settings = json.loads(cipher.decrypt(original_settings[len(header):nonce_end],
                                                  original_settings[nonce_end:], settings_aad))
            entry_path = profile / envelope["entry_directory"] / (entry_id + ".enc")
            for path, payload, aad, command, code in (
                (settings_path, settings, settings_aad, ("status",), "local_history_settings_invalid"),
                (entry_path, entry, entry_id.encode("ascii"), ("show", entry_id), "local_history_data_invalid"),
            ):
                original = path.read_bytes()
                for invalid_version in (2, True):
                    with self.subTest(schema=payload["schema_id"], version=invalid_version):
                        malformed = {**payload, "schema_version": invalid_version}
                        nonce = os.urandom(envelope["nonce_bytes"])
                        ciphertext = header + nonce + cipher.encrypt(nonce, json.dumps(malformed).encode(), aad)
                        path.write_bytes(ciphertext)
                        try:
                            failure = self.history(*command, expected=2)
                            self.assertEqual(failure["error"]["code"], code)
                            self.assertEqual(path.read_bytes(), ciphertext)
                            export = self.root / "unsupported-export.json"
                            self.history("export", "--output", str(export), expected=2)
                            self.assertFalse(export.exists())
                        finally:
                            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
