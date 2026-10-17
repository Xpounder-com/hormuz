"""Terminal adapter for the customer-local coding-client router."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import threading

from . import __version__
from .commands import client as client_commands
from .commands import history as history_commands
from .commands import capabilities as capability_commands
from . import chat_task
from .commands import benchmark as benchmark_commands
from .config import ConfigError, GatewayConfig
from .contracts import contract_manifest
from .evidence import EvidenceStorageError
from .server import GatewayServer
from .store import StorageSchemaError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hormuz", description="Local routing for existing coding clients.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", default=os.environ.get("HORMUZ_CONFIG", "hormuz.json"),
                        help="Router JSON configuration path (or HORMUZ_CONFIG)")
    parser.add_argument("--verbose", action="store_true", help="Enable content-free request logs")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="Run the customer-local router")
    disable = commands.add_parser("off", help="Disable Hormuz and restore the connected Codex settings")
    disable.add_argument("--client-home", help="Codex home (default CODEX_HOME or ~/.codex)")
    disable.add_argument("--generation", help="Disconnect only the receipt generation that you own")
    disable.set_defaults(client="codex", client_command="disconnect")
    contract = commands.add_parser("contract", help="Inspect versioned routing and historical evidence contracts")
    contract.add_subparsers(dest="contract_command", required=True).add_parser("manifest")
    client_commands.add_client_commands(commands)
    history_commands.add_history_commands(commands)
    capability_commands.add_commands(commands)
    chat_task.add_commands(commands)
    benchmark_commands.add_commands(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "contract":
        print(json.dumps(contract_manifest(), indent=2, sort_keys=True))
        return 0
    if args.command == "history":
        return history_commands.run(args)
    if args.command == "chat":
        return chat_task.run(args)
    if args.command == "benchmark":
        return benchmark_commands.run(args)
    if args.command in {"request", "policy", "model", "usage"}:
        return capability_commands.run(args)
    if args.command == "auth":
        if args.auth_command == "local-token":
            return client_commands.local_token(args)
        return client_commands._auth_token(args.env)
    if args.command == "off" or args.command == "client" and args.client_command != "config":
        return client_commands.run_lifetime(args)
    try:
        config = GatewayConfig.load(args.config)
        if args.command == "client":
            return client_commands._client(config, args)
        return _serve(config)
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except (StorageSchemaError, EvidenceStorageError) as error:
        print(f"storage error: {error.code}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("router unavailable: inspect configuration and local storage", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _serve(config: GatewayConfig) -> int:
    server = GatewayServer(config)
    stop_started = threading.Event()
    shutdown_thread = None

    def stop(_signum, _frame):
        nonlocal shutdown_thread
        server.begin_drain()
        if not stop_started.is_set():
            stop_started.set()
            shutdown_thread = threading.Thread(target=server.shutdown, name="hormuz-shutdown", daemon=True)
            shutdown_thread.start()

    previous_term = signal.signal(signal.SIGTERM, stop)
    previous_int = signal.signal(signal.SIGINT, stop)
    try:
        print(f"Hormuz router listening on http://{config.listen.host}:{config.listen.port}", flush=True)
        server.serve_forever()
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
        if shutdown_thread is not None:
            shutdown_thread.join(timeout=2)
        server.server_close()
    return 0
