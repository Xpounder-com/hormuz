"""Read-only cloud inspection at the shared coding-client workflow boundary."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from hormuz.contracts import ContractValidationError, validate_contract, validate_contract_manifest
from tests import test_connected_client_lifetime_workflow as lifetime_workflow
from tests import test_execution_destination_workflow as destination_workflow


class CloudReadinessWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "codex"
        self.home.mkdir(mode=0o700)
        self.executable = self.root / "codex-fixture"
        self.executable.write_text(lifetime_workflow.CODEX.replace("#!PYTHON", "#!" + sys.executable, 1))
        self.executable.chmod(0o700)
        self.path = self.root / "router.json"
        self.environment = {**os.environ, "CODEX_HOME": str(self.home),
                            "DESTINATION_WORKFLOW_TOKEN": "fixture-router-token",
                            "DESTINATION_CLOUD_KEY": "PRIVATE_CLOUD_CREDENTIAL_SENTINEL"}
        self.settings = self.home / "config.toml"
        self.settings.write_text('# personal preference\nmodel = "original-model"\n')

    def command(self, verb="cloud-readiness", *options, check=True):
        result = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(self.path),
            "client", verb, "codex", "--client-home", str(self.home),
            *(["--executable", str(self.executable)] if verb in {"cloud-readiness", "connect"} else []),
            *options], env=self.environment, capture_output=True, text=True, timeout=15)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def configuration(self, local, cloud):
        value = destination_workflow.ExecutionDestinationWorkflowTests.configuration(self, local, cloud)
        value["model_routes"]["cloud-code"] = {"protocol": "openai", "upstream_model": "fixture-cloud-model"}
        value["policies"]["organization"]["allowed_models"].append("cloud-code")
        return value

    def write_configuration(self, value):
        self.path.write_text(json.dumps(value))

    def inspect(self, *options):
        result = self.command("cloud-readiness", *options)
        receipt = json.loads(result.stdout)
        validate_contract(receipt)
        self.assertEqual(receipt["scope"], "configuration_only")
        self.assertNotIn("PRIVATE_CLOUD_CREDENTIAL_SENTINEL", result.stdout + result.stderr)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        self.assertEqual(receipt["effects"], {"settings_changed": False, "credentials_read": False,
                                             "provider_requests": 0})
        return receipt

    def test_local_only_preflight_keeps_credentials_settings_and_providers_untouched(self):
        with destination_workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             destination_workflow.provider("CLOUD_UNUSED") as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value["route_admission"] = {"mode": "local_only", "local_routes": {}}
            credential = self.root / "unreadable-credential"
            os.mkfifo(credential, mode=0o600)
            value["identities"][0].pop("token_env")
            value["identities"][0]["token_file"] = str(credential)
            self.write_configuration(value)
            auth = self.home / "auth.json"
            os.mkfifo(auth, mode=0o600)
            before = self.settings.read_bytes(), set(self.home.iterdir()), self.path.read_bytes()
            receipt = self.inspect()
            self.assertEqual(receipt["configuration"]["status"], "available")
            self.assertEqual(receipt["configuration"]["routing_mode"], "local_only")
            paths = {path["id"]: path for path in receipt["paths"]}
            self.assertEqual(paths["api_key"]["status"], "blocked")
            for path in paths.values():
                self.assertIn("local_only_forbids_cloud", [blocker["code"] for blocker in path["blockers"]])
            self.assertEqual(paths["native_codex_gateway"]["status"], "not_implemented")
            self.assertEqual(paths["chatgpt_app"]["status"], "not_implemented")
            self.assertEqual(before, (self.settings.read_bytes(), set(self.home.iterdir()), self.path.read_bytes()))
            self.assertEqual(local_requests, [])
            self.assertEqual(cloud_requests, [])

    def test_local_only_remains_visible_when_selected_actor_is_unknown(self):
        value = self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1")
        value["route_admission"] = {"mode": "local_only", "local_routes": {}}
        self.write_configuration(value)
        receipt = self.inspect("--actor", "unknown-actor")
        for path in receipt["paths"]:
            codes = [blocker["code"] for blocker in path["blockers"]]
            self.assertIn("actor_unavailable", codes)
            self.assertIn("local_only_forbids_cloud", codes)

    def test_subscription_paths_do_not_require_a_paid_api_route_or_credential(self):
        value = self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1")
        del value["model_routes"]["cloud-code"]
        value["policies"]["organization"]["allowed_models"] = ["local-code"]
        self.write_configuration(value)
        receipt = self.inspect()
        self.assertEqual(receipt["paths"][0]["status"], "blocked")
        for path in receipt["paths"][1:]:
            self.assertEqual(path["billing"], "chatgpt_plan")
            self.assertEqual(path["status"], "not_implemented")
            codes = {blocker["code"] for blocker in path["blockers"]}
            self.assertTrue(codes.isdisjoint({"cloud_route_missing", "cloud_models_not_allowed", "credentials_unverified"}))
            self.assertIn("account_consent_eligibility_unverified", codes)

    def test_api_declaration_remains_unverified_and_inspection_preserves_active_connection(self):
        with destination_workflow.provider("LOCAL_RESULT") as (local, local_requests), \
             destination_workflow.provider("CLOUD_UNUSED") as (cloud, cloud_requests):
            self.write_configuration(self.configuration(local, cloud))
            original = self.settings.read_bytes()
            try:
                connected = json.loads(self.command("connect").stdout)
                settings = self.settings.read_bytes()
                receipt = self.inspect()
                self.assertEqual(receipt["client"]["version_check"], "pinned")
                self.assertEqual(receipt["configuration"], {"status": "available", "routing_mode": "configured",
                    "codex_allowed": True, "cloud_routes": 1, "allowed_cloud_routes": 1})
                api = receipt["paths"][0]
                self.assertEqual((api["status"], api["billing"]), ("unverified", "api_usage"))
                self.assertEqual({b["code"] for b in api["blockers"]}, {"credentials_unverified", "provider_unqualified"})
                self.assertEqual(settings, self.settings.read_bytes())
                status = json.loads(self.command("status").stdout)
                self.assertEqual((status["generation"], status["phase"]), (connected["generation"], "connected"))
                result = subprocess.run([str(self.executable)], env=self.environment,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output["status"], 200)
                self.assertEqual(output["body"]["output"][0]["content"][0]["text"], "LOCAL_RESULT")
                self.assertEqual(len(local_requests), 1)
                self.assertEqual(cloud_requests, [])
            finally:
                self.command("disconnect", check=False)
            self.assertEqual(original, self.settings.read_bytes())

    def test_effective_actor_and_client_restrictions_block_api_declarations(self):
        value = self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1")
        for case in ("actor_models", "team_client", "identity_client"):
            with self.subTest(case=case):
                current = json.loads(json.dumps(value))
                if case == "actor_models":
                    current["policies"]["actors"] = {"developer": {"allowed_models": ["local-code"]}}
                elif case == "team_client":
                    current["policies"]["teams"] = {"local": {"allowed_clients": ["claude"]}}
                else:
                    current["identities"][0]["allowed_clients"] = ["claude"]
                self.write_configuration(current)
                receipt = self.inspect()
                api = receipt["paths"][0]
                self.assertEqual(api["status"], "blocked")
                self.assertEqual(receipt["configuration"]["allowed_cloud_routes"], 0)
                self.assertIn("cloud_models_not_allowed" if case == "actor_models" else "client_not_allowed",
                              [blocker["code"] for blocker in api["blockers"]])

    def test_missing_and_unsafe_configuration_returns_actionable_content_free_receipt(self):
        for case in ("missing", "invalid", "duplicate", "fifo", "symlink", "storage_symlink_loop"):
            with self.subTest(case=case):
                if self.path.exists() or self.path.is_symlink():
                    self.path.unlink()
                if case == "invalid":
                    self.path.write_text('{"PRIVATE_INPUT_SENTINEL": true}')
                elif case == "duplicate":
                    self.path.write_text('{"identities": [], "identities": [], "PRIVATE_INPUT_SENTINEL": true}')
                elif case == "fifo":
                    os.mkfifo(self.path, mode=0o600)
                elif case == "symlink":
                    self.path.symlink_to(self.settings)
                elif case == "storage_symlink_loop":
                    storage = self.root / "storage-loop"
                    storage.symlink_to(storage.name)
                    value = self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1")
                    value["database"] = storage.name
                    self.write_configuration(value)
                before = self.settings.read_bytes(), set(self.home.iterdir())
                receipt = self.inspect()
                if case == "storage_symlink_loop":
                    # Resolution differs across Python versions; neither outcome
                    # validates storage readiness or grants execution authority.
                    self.assertIn(receipt["configuration"]["status"], {"available", "invalid"})
                    self.assertIn(receipt["paths"][0]["status"], {"blocked", "unverified"})
                else:
                    self.assertEqual(receipt["configuration"]["status"], "unavailable" if case == "missing" else "invalid")
                    self.assertEqual(receipt["paths"][0]["status"], "blocked")
                self.assertNotIn("PRIVATE_INPUT_SENTINEL", json.dumps(receipt))
                self.assertEqual(before, (self.settings.read_bytes(), set(self.home.iterdir())))

    def test_desktop_does_not_run_or_get_qualified_by_the_terminal_executable(self):
        self.write_configuration(self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1"))
        self.executable.write_text("#!" + sys.executable + "\nimport pathlib\npathlib.Path(__file__).with_name('executed').touch()\n")
        receipt = self.inspect("--surface", "desktop")
        self.assertFalse((self.root / "executed").exists())
        self.assertEqual(receipt["client"]["version_check"], "not_performed")
        for path in receipt["paths"]:
            self.assertIn("desktop_unqualified", [blocker["code"] for blocker in path["blockers"]])

    def test_unsupported_cli_and_managed_settings_do_not_activate_or_read_authorization(self):
        self.write_configuration(self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1"))
        (self.root / "client-version.txt").write_text("0.160.0")
        managed = self.home / "requirements.toml"
        os.mkfifo(managed, mode=0o600)
        before = self.settings.read_bytes(), set(self.home.iterdir())
        receipt = self.inspect()
        self.assertEqual(receipt["client"]["version_check"], "unsupported")
        codes = {blocker["code"] for blocker in receipt["paths"][0]["blockers"]}
        self.assertTrue({"client_version_unsupported", "managed_configuration"} <= codes)
        self.assertEqual(before, (self.settings.read_bytes(), set(self.home.iterdir())))

    def test_api_preflight_does_not_require_secret_environment_or_create_client_home(self):
        self.write_configuration(self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1"))
        self.environment.pop("DESTINATION_WORKFLOW_TOKEN")
        self.environment.pop("DESTINATION_CLOUD_KEY")
        self.settings.unlink()
        self.home.rmdir()
        receipt = self.inspect()
        self.assertEqual(receipt["configuration"]["status"], "available")
        self.assertEqual(receipt["client"]["version_check"], "pinned")
        self.assertEqual(receipt["paths"][0]["status"], "unverified")
        self.assertEqual({blocker["code"] for blocker in receipt["paths"][0]["blockers"]},
                         {"credentials_unverified", "provider_unqualified"})
        self.assertFalse(self.home.exists())
        self.assertFalse((self.root / "usage.sqlite3").exists())

    def test_version_check_timeout_is_bounded_and_reaps_its_owned_process(self):
        self.write_configuration(self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1"))
        self.executable.write_text("#!" + sys.executable + "\nimport os,pathlib,time\n"
            "pathlib.Path(__file__).with_name('version-pid').write_text(str(os.getpid()))\ntime.sleep(20)\n")
        started = time.monotonic()
        receipt = self.inspect()
        self.assertLess(time.monotonic() - started, 7)
        self.assertEqual(receipt["client"]["version_check"], "unsupported")
        pid = int((self.root / "version-pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX process groups")
    def test_version_check_timeout_stops_descendants_after_the_parent_exits(self):
        self.write_configuration(self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1"))
        self.executable.write_text("#!" + sys.executable + "\nimport os,pathlib,time\n"
            "child = os.fork()\n"
            "if child:\n"
            " pathlib.Path(__file__).with_name('descendant-pid').write_text(str(child))\n"
            " os._exit(0)\n"
            "for tick in range(400):\n"
            " pathlib.Path(__file__).with_name('descendant-heartbeat').write_text(str(tick))\n"
            " time.sleep(0.05)\n")
        try:
            started = time.monotonic()
            receipt = self.inspect()
            self.assertLess(time.monotonic() - started, 7)
            self.assertEqual(receipt["client"]["version_check"], "unsupported")
            heartbeat = self.root / "descendant-heartbeat"
            stopped = heartbeat.read_bytes()
            time.sleep(0.2)
            self.assertEqual(heartbeat.read_bytes(), stopped, "version-check descendant is still running")
        finally:
            pid_file = self.root / "descendant-pid"
            if pid_file.exists():
                try:
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_missing_cli_and_noisy_version_output_remain_content_free(self):
        self.write_configuration(self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1"))
        missing = self.inspect("--executable", str(self.root / "missing-client"))
        self.assertEqual(missing["client"]["version_check"], "unavailable")
        self.executable.write_text("#!" + sys.executable + "\nprint('PRIVATE_CLOUD_CREDENTIAL_SENTINEL' * 200)\n")
        noisy = self.inspect()
        self.assertEqual(noisy["client"]["version_check"], "unsupported")

    def test_receipt_rejects_false_eligibility_and_missing_authorization_blockers(self):
        value = self.configuration("http://127.0.0.1:1", "https://cloud.example.invalid/v1")
        value["route_admission"] = {"mode": "local_only", "local_routes": {}}
        self.write_configuration(value)
        receipt = self.inspect()
        false_eligibility = json.loads(json.dumps(receipt))
        false_eligibility["paths"][0]["status"] = "unverified"
        with self.assertRaises(ContractValidationError):
            validate_contract(false_eligibility)
        for case in ("consent", "billing", "counts", "version", "desktop", "shape", "effects", "content",
                     "missing_cli_action", "unsupported_cli_action", "missing_route_action"):
            with self.subTest(case=case):
                invalid = json.loads(json.dumps(receipt))
                if case == "consent":
                    invalid["paths"][1]["blockers"] = [blocker for blocker in invalid["paths"][1]["blockers"]
                                                        if blocker["code"] != "account_consent_eligibility_unverified"]
                elif case == "billing":
                    invalid["paths"][0]["billing"] = "chatgpt_plan"
                elif case == "counts":
                    invalid["configuration"]["allowed_cloud_routes"] = 2
                elif case == "version":
                    invalid["client"]["pinned_cli_version"] = "PRIVATE_CONTENT"
                elif case == "desktop":
                    invalid["client"]["surface"] = "desktop"
                elif case == "shape":
                    invalid["client"]["surface"] = []
                elif case == "effects":
                    invalid["effects"]["provider_requests"] = True
                elif case == "missing_cli_action":
                    invalid["client"]["version_check"] = "unavailable"
                elif case == "unsupported_cli_action":
                    invalid["client"]["version_check"] = "unsupported"
                elif case == "missing_route_action":
                    invalid["configuration"].update(cloud_routes=0, allowed_cloud_routes=0)
                else:
                    invalid["private_content"] = "PRIVATE_CONTENT"
                with self.assertRaises(ContractValidationError):
                    validate_contract(invalid)

    def test_versioned_fixture_and_public_manifest_preserve_the_inspection_contract(self):
        receipt = json.loads((Path(__file__).parent / "fixtures/cloud-readiness-v1.json").read_text())
        validate_contract(receipt)
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual([path["status"] for path in receipt["paths"]],
                         ["blocked", "not_implemented", "not_implemented"])
        result = subprocess.run([sys.executable, "-m", "hormuz", "contract", "manifest"],
                                env=self.environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        validate_contract_manifest(manifest)
        entries = [entry for entry in manifest["schemas"] if entry["schema_id"] == "hormuz.cloud-readiness"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["schema_version"], 1)
        self.assertEqual(set(entries[0]["fields"]), set(receipt))

    def test_receipt_rejects_recovery_actions_that_contradict_its_facts(self):
        receipt = json.loads((Path(__file__).parent / "fixtures/cloud-readiness-v1.json").read_text())
        for code, action in (
            ("actor_unavailable", "Choose an actor declared in the router configuration."),
            ("client_not_allowed", "Review the selected identity and effective Codex policy."),
            ("client_unavailable", "Install or select the pinned Codex CLI executable."),
            ("client_version_unsupported", "Use the pinned CLI version; it does not qualify subscription forwarding."),
            ("desktop_unqualified", "Qualify the actual desktop app and its bundled engine separately."),
            ("configuration_invalid", "Repair the router configuration before attempting connection."),
            ("configuration_unavailable", "Supply a readable router configuration; no settings were changed."),
            ("cloud_models_not_allowed", "Review effective model policy before selecting a cloud route."),
        ):
            with self.subTest(code=code):
                invalid = json.loads(json.dumps(receipt))
                invalid["paths"][0]["blockers"].append({"code": code, "action": action})
                with self.assertRaises(ContractValidationError):
                    validate_contract(invalid)
        invalid = json.loads(json.dumps(receipt))
        invalid["configuration"].update(cloud_routes=1, allowed_cloud_routes=1)
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid)
        invalid = json.loads(json.dumps(receipt))
        invalid["configuration"]["routing_mode"] = "configured"
        with self.assertRaises(ContractValidationError):
            validate_contract(invalid)


if __name__ == "__main__":
    unittest.main()
