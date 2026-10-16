"""Rebranding keeps public entry points and existing local data usable."""

from contextlib import chdir
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hormuz.cli import build_parser
from hormuz.connected_client import ConnectedClientError, _control_directory, _parse_record
from hormuz.local_history import LocalHistory


class KotoBrandingWorkflowTests(unittest.TestCase):
    def test_koto_command_routes_then_restores_the_existing_client(self):
        from tests.test_connected_client_lifetime_workflow import ConnectedClientLifetimeWorkflowTests
        fixture = ConnectedClientLifetimeWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)

        def command(verb):
            result = subprocess.run([sys.executable, "-m", "koto", "--config", str(fixture.path),
                "client", verb, "codex", "--client-home", str(fixture.home),
                *(["--executable", str(fixture.executable)] if verb == "connect" else [])],
                env=fixture.environment, capture_output=True, text=True, timeout=18)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        with fixture.configured() as (local_requests, cloud_requests):
            command("connect")
            self.assertIn('name = "Koto"', (fixture.home / "config.toml").read_text())
            self.assertEqual(fixture.client()["status"], 200)
            self.assertEqual((len(local_requests), len(cloud_requests)), (1, 0))
            self.assertEqual(command("status")["phase"], "connected")
            self.assertEqual(command("disconnect")["phase"], "disconnected")
            restored = fixture.client()
            self.assertEqual((restored["model"], restored["provider"]), ("original-model", "original"))

    def test_new_and_legacy_module_entry_points_show_koto_help(self):
        for module in ("koto", "hormuz"):
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, "-m", module, "--help"],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage: koto ", result.stdout)
                self.assertIn("KOTO_CONFIG", result.stdout)

    def test_new_config_default_and_existing_config_fallback(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(build_parser().parse_args([]).config, "koto.json")
            Path("hormuz.json").write_text("{}")
            self.assertEqual(build_parser().parse_args([]).config, "hormuz.json")
            Path("koto.json").write_text("{}")
            self.assertEqual(build_parser().parse_args([]).config, "koto.json")
            Path("koto.json").unlink()
            Path("koto.json").symlink_to("missing-config.json")
            self.assertEqual(build_parser().parse_args([]).config, "koto.json")

    def test_new_config_environment_takes_precedence_and_explicit_path_wins(self):
        with patch.dict(os.environ, {"KOTO_CONFIG": "new.json", "HORMUZ_CONFIG": "old.json"}, clear=True):
            self.assertEqual(build_parser().parse_args([]).config, "new.json")
            self.assertEqual(build_parser().parse_args(["--config", "explicit.json"]).config, "explicit.json")
        with patch.dict(os.environ, {"HORMUZ_CONFIG": "old.json"}, clear=True):
            self.assertEqual(build_parser().parse_args([]).config, "old.json")

    def test_auth_helper_defaults_to_koto_and_accepts_legacy_environment(self):
        for environment, expected in (({}, "KOTO_GATEWAY_TOKEN"),
                                      ({"HORMUZ_GATEWAY_TOKEN": "synthetic-old"}, "HORMUZ_GATEWAY_TOKEN"),
                                      ({"HORMUZ_GATEWAY_TOKEN": "synthetic-old", "KOTO_GATEWAY_TOKEN": "synthetic-new"},
                                       "KOTO_GATEWAY_TOKEN")):
            with self.subTest(expected=expected), patch.dict(os.environ, environment, clear=True):
                self.assertEqual(build_parser().parse_args(["auth", "token"]).env, expected)

    def test_encrypted_history_and_consent_survive_default_directory_change(self):
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve()
                source = home / "router.json"
                legacy = (home / "Library/Application Support/Hormuz/history-v1" if platform == "darwin"
                          else home / ".local/share/hormuz/history-v1")
                history = LocalHistory(source, environ={"HOME": str(home), "HORMUZ_HISTORY_HOME": str(legacy)})
                history.enable("local-user")
                key = (history.directory / "device.key").read_bytes()
                with patch("hormuz.local_history.sys.platform", platform):
                    current = LocalHistory(source, environ={"HOME": str(home)})
                    another = LocalHistory(home / "another-router.json", environ={"HOME": str(home)})
                    another.enable("another-user")
                    current = LocalHistory(source, environ={"HOME": str(home)})
                self.assertEqual(current.directory, history.directory)
                self.assertTrue(current.status()["enabled"])
                self.assertEqual((current.directory / "device.key").read_bytes(), key)
                current.disable()
                self.assertFalse(history.status()["enabled"])

    def test_new_history_override_wins_and_fresh_install_uses_koto(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            source = home / "router.json"
            environment = {"HOME": str(home), "KOTO_HISTORY_HOME": str(home / "new"),
                           "HORMUZ_HISTORY_HOME": str(home / "old")}
            self.assertEqual(LocalHistory(source, environ=environment).directory.parent, home / "new")
            with patch("hormuz.local_history.sys.platform", "darwin"):
                current = LocalHistory(source, environ={"HOME": str(home)})
                self.assertEqual(current.directory.parent, home / "Library/Application Support/Koto/history-v1")
            with patch("hormuz.local_history.sys.platform", "linux"):
                current = LocalHistory(source, environ={"HOME": str(home)})
                self.assertEqual(current.directory.parent, home / ".local/share/koto/history-v1")

    def test_existing_connection_journal_retains_ownership_and_provider_bytes(self):
        generation = "a" * 32
        provider = "hormuz_" + generation
        for name in ("Hormuz", "Koto"):
            block = (f'\n[model_providers.{provider}]\nname = "{name}"\n'
                     'base_url = "http://127.0.0.1:12345/v1"\nwire_api = "responses"\n'
                     'requires_openai_auth = false\nenv_key = "LOCAL_ROUTER_TOKEN"\n')
            record = {"schema_id": "hormuz.connected-client-journal", "schema_version": 1,
                      "generation": generation, "phase": "connected", "client_version": "0.147.0",
                      "installed": {"model": "local", "model_provider": provider, "web_search": "disabled"},
                      "original": {"model": None, "model_provider": None, "web_search": None},
                      "provider_block": block, "gateway_config": "/tmp/router.json", "gateway_digest": "b" * 64,
                      "actor_id": "local-user", "control": str(_control_directory() / (generation + ".sock"))}
            with self.subTest(name=name):
                self.assertEqual(_parse_record(json.dumps(record))["provider_block"], block)
                for altered in (block.replace(name, "Unowned"), block + 'extra = true\n'):
                    with self.assertRaises(ConnectedClientError):
                        _parse_record(json.dumps({**record, "provider_block": altered}))


if __name__ == "__main__":
    unittest.main()
