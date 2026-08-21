from __future__ import annotations

import argparse
import importlib.util
import io
import json
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

from hormuz.cli import build_parser, main
from hormuz.config import ConfigError, GatewayConfig
from hormuz.server import GatewayServer, serve_in_thread


ROOT = Path(__file__).resolve().parents[1]
TEST_ENVIRONMENT = {"HORMUZ_TOKEN": "test-identity-token"}


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class CorePackageBoundaryTests(unittest.TestCase):
    def _config(self) -> GatewayConfig:
        return GatewayConfig.load(ROOT / "config.example.json", environ=TEST_ENVIRONMENT)

    def test_core_has_no_context_module_or_active_cli_command(self) -> None:
        self.assertIsNone(importlib.util.find_spec("hormuz.context"))
        parser = build_parser()
        subparser_action = next(
            action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        )
        self.assertNotIn("context-pack", subparser_action.choices)

        stderr = io.StringIO()
        with mock.patch("hormuz.cli.GatewayConfig.load") as load_config, redirect_stderr(stderr):
            self.assertEqual(main(["--config", "missing.json", "context-pack"]), 2)
        load_config.assert_not_called()
        self.assertIn("context_experiment_moved", stderr.getvalue())

    def test_legacy_context_configuration_fails_closed(self) -> None:
        payload = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            for legacy_setting in (
                {"context_database": "./hormuz-context.sqlite3"},
                {"policies": {**payload["policies"], "organization": {**payload["policies"]["organization"], "context_injection": {"mode": "required"}}}},
            ):
                candidate = {**payload, **legacy_setting}
                config_path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, "context_experiment_moved"):
                    GatewayConfig.load(config_path, environ=TEST_ENVIRONMENT)

    def test_gateway_starts_without_context_storage_or_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_config = self._config()
            config = replace(
                base_config,
                database_path=root / "usage.sqlite3",
                listen=replace(base_config.listen, port=_available_port()),
            )
            server = GatewayServer(config)
            thread = serve_in_thread(server)
            connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            try:
                connection.request("POST", "/v1/context/packs", body=b"{}")
                response = connection.getresponse()
                body = json.loads(response.read())

                self.assertEqual(response.status, 404)
                self.assertEqual(body["error"]["code"], "not_found")
                self.assertTrue((root / "usage.sqlite3").exists())
                self.assertFalse(any("context" in path.name.lower() for path in root.iterdir()))
                self.assertFalse(any(name == "hormuz.context" or name.startswith("hormuz.context.") for name in sys.modules))
            finally:
                connection.close()
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()


if __name__ == "__main__":
    unittest.main()
