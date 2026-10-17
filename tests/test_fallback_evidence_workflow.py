"""Fallback explanations observed through connected clients and public history CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from tests import test_execution_destination_workflow as workflow


class FallbackEvidenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow.ExecutionDestinationWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = self.fixture.root / "gateway.json"
        self.fixture.environment["HORMUZ_HISTORY_HOME"] = str(self.fixture.root / "history")

    def command(self, *arguments):
        result = subprocess.run(
            [sys.executable, "-m", "hormuz", "--config", str(self.config), *arguments],
            env={**os.environ, **self.fixture.environment},
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def capacity_fallback(self, status):
        with workflow.provider("FALLBACK", status=lambda body: status if body["model"] == "cloud-model" else 200) as (endpoint, received):
            value = self.fixture.configuration(endpoint, endpoint)
            value["model_routes"] = {
                "cloud-code": {"protocol": "openai", "upstream_model": "cloud-model", "failover_alias": "cloud-alternate"},
                "cloud-alternate": {"protocol": "openai", "upstream_model": "cloud-fallback-model"},
            }
            value["policies"]["organization"]["allowed_models"] = ["cloud-code", "cloud-alternate"]
            with self.fixture.connected_client(value) as submit:
                self.command("history", "enable")
                response = submit(request={"model": "cloud-code"})
                self.assertEqual(response["status"], 200, response)
                self.assertIn("x-hormuz-failover", response["headers"])
                entries = self.command("history", "list")["entries"]
                self.assertEqual(len(entries), 2)
                by_model = {entry["routed_model"]: entry for entry in entries}
                original = self.command("request", "explain", by_model["cloud-model"]["id"])
                fallback = self.command("request", "explain", by_model["cloud-fallback-model"]["id"])
                self.assertEqual(original["observation"]["http_status"], status)
                self.assertIs(original["fallback"], False)
                self.assertIs(fallback["fallback"], True)
                self.assertEqual(fallback["observation"]["outcome"], "succeeded")
                retained = self.command("history", "show", by_model["cloud-fallback-model"]["id"])["entry"]
                self.assertIs(retained["routing_evidence"]["fallback"], True)
            self.assertEqual([item["body"]["model"] for item in received], ["cloud-model", "cloud-fallback-model"])

    def test_explanation_marks_successful_capacity_fallback_after_529(self):
        self.capacity_fallback(529)

    def test_explanation_marks_successful_capacity_fallback_after_429(self):
        self.capacity_fallback(429)

    def test_explanation_keeps_policy_fallback_with_capping_and_redaction(self):
        with workflow.provider("POLICY_FALLBACK") as (endpoint, received):
            value = self.fixture.configuration(endpoint, endpoint)
            value["policies"]["organization"]["fallback_model"] = "local-code"
            with self.fixture.connected_client(value) as submit:
                self.command("history", "enable")
                response = submit(request={"model": "unavailable-model", "max_output_tokens": 20,
                                           "input": self.fixture.environment["DESTINATION_WORKFLOW_TOKEN"]})
                self.assertEqual(response["status"], 200, response)
                self.assertEqual(response["headers"]["x-hormuz-policy-decision"], "fallback+capped+redacted")
                self.assertNotIn("x-hormuz-failover", response["headers"])
                entry = self.command("history", "list")["entries"][0]
                explanation = self.command("request", "explain", entry["id"])
                self.assertIs(explanation["fallback"], True)
                self.assertEqual(explanation["policy_decision"], "fallback+capped+redacted")
            self.assertEqual([item["body"]["model"] for item in received], ["fixture-local-model"])

    def test_explanation_marks_ordinary_request_without_fallback(self):
        with workflow.provider("DIRECT") as (endpoint, received):
            with self.fixture.connected_client(self.fixture.configuration(endpoint, endpoint)) as submit:
                self.command("history", "enable")
                response = submit()
                self.assertEqual(response["status"], 200, response)
                self.assertNotIn("x-hormuz-failover", response["headers"])
                entry = self.command("history", "list")["entries"][0]
                explanation = self.command("request", "explain", entry["id"])
                self.assertIs(explanation["fallback"], False)
                self.assertEqual(explanation["policy_decision"], "allowed")
            self.assertEqual(len(received), 1)

    def test_legacy_entry_without_evidence_keeps_fallback_unobserved(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        with workflow.provider("LEGACY_PRIVATE") as (endpoint, received):
            with self.fixture.connected_client(self.fixture.configuration(endpoint, endpoint)) as submit:
                self.command("history", "enable")
                self.assertEqual(submit()["status"], 200)
                entry = self.command("history", "list")["entries"][0]
                profile = Path(self.command("history", "status")["storage_path"])
                # Seed the supported legacy format using its public envelope,
                # then observe compatibility only through public commands.
                envelope = json.loads((Path(__file__).parent / "fixtures/local-history-v1.json").read_text())["encryption_envelope"]
                cipher = AESGCM((profile / envelope["key_file"]).read_bytes())
                path = profile / envelope["entry_directory"] / (entry["id"] + ".enc")
                encrypted = path.read_bytes()
                header = envelope["header_utf8"].encode()
                nonce_end = len(header) + envelope["nonce_bytes"]
                legacy = json.loads(cipher.decrypt(encrypted[len(header):nonce_end], encrypted[nonce_end:], entry["id"].encode()))
                legacy.pop("routing_evidence")
                legacy["schema_version"] = 1
                nonce = os.urandom(envelope["nonce_bytes"])
                path.write_bytes(header + nonce + cipher.encrypt(nonce, json.dumps(legacy).encode(), entry["id"].encode()))
                explanation = self.command("request", "explain", entry["id"])
                self.assertEqual(explanation["fallback"], "unobserved")
                self.assertEqual(explanation["policy_decision"], "unobserved")
                self.assertNotIn("LEGACY_PRIVATE", json.dumps(explanation))
                self.assertEqual(self.command("history", "show", entry["id"])["entry"]["schema_version"], 1)
            self.assertEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()
