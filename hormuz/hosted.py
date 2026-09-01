"""Explicit operator commands and supervisor for hosted authentication staging.

The container starts closed in maintenance. No initialization, administrator,
invitation, migration or recovery is inferred from an application restart.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from ._hosted_config import BACKEND_PORT, SECRET_NAMES, HostedError, load_profile
from ._hosted_state import check_initialized, check_recovered_closed, initialize, restore, snapshot, state_lock


def runtime_settings() -> dict[str, str]:
    # The inventory reviews each direct read. Never inherit the full deployment
    # environment into the proxy or into the private gateway child.
    return {
        "HORMUZ_CONFIG": os.environ.get("HORMUZ_CONFIG", "/etc/secrets/hormuz-hosted.json"),
        "HORMUZ_HOSTED_MODE": os.environ.get("HORMUZ_HOSTED_MODE", "maintenance"),
        "PORT": os.environ.get("PORT", "10000"),
        "HORMUZ_INGRESS_CREDENTIAL": os.environ.get("HORMUZ_INGRESS_CREDENTIAL", ""),
        "HORMUZ_SESSION_MASTER_KEY": os.environ.get("HORMUZ_SESSION_MASTER_KEY", ""),
        "HORMUZ_OIDC_CLIENT_SECRET": os.environ.get("HORMUZ_OIDC_CLIENT_SECRET", ""),
    }


def proxy_settings(settings: dict[str, str], *, active: bool) -> dict[str, str]:
    port = settings["PORT"]
    if not port.isascii() or not port.isdecimal() or not 1024 <= int(port) <= 65535 or int(port) == BACKEND_PORT:
        raise HostedError("hosted_public_port_invalid")
    child = {"PORT": str(int(port)), "XDG_CONFIG_HOME": "/tmp/caddy/config", "XDG_DATA_HOME": "/tmp/caddy/data"}
    if active:
        child["HORMUZ_INGRESS_CREDENTIAL"] = settings["HORMUZ_INGRESS_CREDENTIAL"]
    return child


def stop_child(process, seconds: float) -> bool:
    if process is None or process.poll() is not None:
        return True
    process.terminate()
    try:
        process.wait(timeout=seconds)
        return process.returncode == 0
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
        return False


def _spawn(arguments, settings):
    return subprocess.Popen(arguments, env=settings, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def _backend_ready(process, config, stopped) -> None:
    request = Request(f"http://127.0.0.1:{BACKEND_PORT}/ready", headers={
        "Host": urlsplit(config.session_broker.public_base_url).netloc,
        "X-Hormuz-Ingress-Credential": config.ingress.credential,
    })
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 20
    while not stopped.is_set() and process.poll() is None and time.monotonic() < deadline:
        try:
            with opener.open(request, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        stopped.wait(0.1)
    raise HostedError("hosted_backend_not_ready")


def supervise(settings: dict[str, str], config_path: Path) -> int:
    mode = settings["HORMUZ_HOSTED_MODE"]
    if mode not in {"maintenance", "active"}:
        raise HostedError("hosted_mode_invalid")
    child_settings = proxy_settings(settings, active=mode == "active")
    stopped = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stopped.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    backend = proxy = None
    successful = False
    try:
        if mode == "active":
            config = load_profile(config_path, settings)
            check_initialized(config)
            backend = _spawn([sys.executable, "-I", "-m", "hormuz.hosted", "--config", str(config_path), "backend"],
                             {name: settings[name] for name in SECRET_NAMES})
            _backend_ready(backend, config, stopped)
        proxy = _spawn(["/usr/bin/caddy", "run", "--config", f"/etc/hormuz/caddy/{mode}.Caddyfile", "--adapter", "caddyfile"], child_settings)
        print(json.dumps({"event": "hosted_starting", "mode": mode, "inference_enabled": False}), flush=True)
        while not stopped.wait(0.1):
            if proxy.poll() is not None or backend is not None and backend.poll() is not None:
                raise HostedError("hosted_child_exited")
        successful = True
    finally:
        # Stop admission first, then let accepted authentication transactions
        # drain. This budget stays below Render's default 30-second SIGKILL.
        proxy_clean = stop_child(proxy, 5)
        backend_clean = stop_child(backend, 15)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        print(json.dumps({"event": "hosted_stopped", "clean": proxy_clean and backend_clean}), flush=True)
    return 0 if successful and proxy_clean and backend_clean else 1


def backend(config) -> None:
    from ._hosted_server import StagingGatewayServer

    with state_lock(config, exclusive=False):
        server = StagingGatewayServer(config)
        stopping = threading.Event()

        def terminate(*_):
            server.begin_drain()
            if not stopping.is_set():
                stopping.set()
                threading.Thread(target=server.shutdown, daemon=True).start()

        previous = {sig: signal.signal(sig, terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            server.server_close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main(argv=None) -> int:
    from .commands.onboarding import add_onboarding_commands, run as run_team
    from ._hosted_backup import export_backup, read_backup_key, restore_backup, verify_backup

    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    settings = runtime_settings()
    parser = argparse.ArgumentParser(description="Provider-free hosted authentication staging; defaults to maintenance.")
    parser.add_argument("--config", type=Path, default=Path(settings["HORMUZ_CONFIG"]))
    commands = parser.add_subparsers(dest="command")
    for name in ("serve", "backend", "initialize", "check", "recovery-check"):
        commands.add_parser(name)
    commands.add_parser("snapshot").add_argument("--output-directory", type=Path, required=True)
    commands.add_parser("restore").add_argument("--snapshot-directory", type=Path, required=True)
    for name in ("backup-export", "backup-verify", "backup-restore"):
        command = commands.add_parser(name)
        command.add_argument("--key-file", type=Path, required=True)
        command.add_argument(
            "--output-file" if name == "backup-export" else "--archive-file",
            type=Path,
            required=True,
        )
    add_onboarding_commands(commands)
    args = parser.parse_args(argv)
    try:
        if args.command in {None, "serve"}:
            return supervise(settings, args.config)
        if args.command == "backup-verify":
            result = verify_backup(args.archive_file, read_backup_key(args.key_file))
            print(json.dumps({"event": "hosted_operator_complete", "operation": args.command,
                              "inference_enabled": False, **result}, sort_keys=True))
            return 0
        config = load_profile(args.config, settings)
        result = {}
        if args.command == "backend":
            backend(config)
        elif args.command == "initialize":
            initialize(config)
        elif args.command == "snapshot":
            snapshot(config, args.output_directory)
        elif args.command == "restore":
            restore(config, args.snapshot_directory)
        elif args.command == "backup-export":
            key = read_backup_key(args.key_file, session_master_key=config.session_broker.master_key)
            result = export_backup(config, args.output_file, key)
        elif args.command == "backup-restore":
            key = read_backup_key(args.key_file, session_master_key=config.session_broker.master_key)
            result = restore_backup(config, args.archive_file, key)
        elif args.command == "recovery-check":
            result = check_recovered_closed(config)
        else:
            with state_lock(config, exclusive=False):
                check_initialized(config)
                if args.command == "team":
                    return run_team(config, args)
        print(json.dumps({"event": "hosted_operator_complete", "operation": args.command,
                          "inference_enabled": False, **result}, sort_keys=True))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, HostedError) else "hosted_operation_failed"
        print(json.dumps({"event": "hosted_operation_failed", "code": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
