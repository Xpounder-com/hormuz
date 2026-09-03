"""Explicit operator commands and supervisor for bounded hosted modes.

The container starts closed in maintenance. No initialization, administrator,
invitation, migration, recovery or provider activation is inferred from an
application restart. Authentication staging and provider traffic use distinct
configuration and process-secret boundaries.
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
from ._hosted_provider import (
    PROVIDER_CHILD_ENV_NAMES,
    PROVIDER_CONFIG_ENV,
    PROVIDER_FAILOVER_REHEARSAL_ENV,
    PROVIDER_MIGRATION_DSN_ENV,
    PROVIDER_OPERATOR_SECRET_NAMES,
    PROVIDER_RUNTIME_DSN_ENV,
    PROVIDER_SECRET_NAMES,
    load_provider_profile,
)
from ._hosted_state import (
    check_initialized,
    check_recovered_closed,
    initialize,
    migrate_usage,
    restore,
    snapshot,
    state_lock,
)
from .postgres import PostgresStorageError


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
        PROVIDER_CONFIG_ENV: os.environ.get(PROVIDER_CONFIG_ENV, "/etc/secrets/hormuz-provider.json"),
        "HORMUZ_OPENAI_PROVIDER_KEY": os.environ.get("HORMUZ_OPENAI_PROVIDER_KEY", ""),
        "HORMUZ_ANTHROPIC_PROVIDER_KEY": os.environ.get("HORMUZ_ANTHROPIC_PROVIDER_KEY", ""),
        PROVIDER_FAILOVER_REHEARSAL_ENV: os.environ.get(PROVIDER_FAILOVER_REHEARSAL_ENV, ""),
        PROVIDER_RUNTIME_DSN_ENV: os.environ.get(PROVIDER_RUNTIME_DSN_ENV, ""),
        PROVIDER_MIGRATION_DSN_ENV: os.environ.get(PROVIDER_MIGRATION_DSN_ENV, ""),
        "RENDER": os.environ.get("RENDER", ""),
        "RENDER_CPU_COUNT": os.environ.get("RENDER_CPU_COUNT", ""),
        "RENDER_EXTERNAL_HOSTNAME": os.environ.get("RENDER_EXTERNAL_HOSTNAME", ""),
        "RENDER_EXTERNAL_URL": os.environ.get("RENDER_EXTERNAL_URL", ""),
        "RENDER_GIT_BRANCH": os.environ.get("RENDER_GIT_BRANCH", ""),
        "RENDER_GIT_COMMIT": os.environ.get("RENDER_GIT_COMMIT", ""),
        "RENDER_GIT_REPO_SLUG": os.environ.get("RENDER_GIT_REPO_SLUG", ""),
        "RENDER_INSTANCE_ID": os.environ.get("RENDER_INSTANCE_ID", ""),
        "RENDER_SERVICE_ID": os.environ.get("RENDER_SERVICE_ID", ""),
        "RENDER_SERVICE_TYPE": os.environ.get("RENDER_SERVICE_TYPE", ""),
        "RENDER_WEB_CONCURRENCY": os.environ.get("RENDER_WEB_CONCURRENCY", ""),
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


def supervise(settings: dict[str, str], config_path: Path, provider_config_path: Path) -> int:
    mode = settings["HORMUZ_HOSTED_MODE"]
    if mode not in {"maintenance", "active", "provider-pilot"}:
        raise HostedError("hosted_mode_invalid")
    child_settings = proxy_settings(settings, active=mode != "maintenance")
    stopped = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stopped.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    backend = proxy = None
    successful = False
    try:
        inference_enabled = mode == "provider-pilot"
        if mode == "active":
            config = load_profile(config_path, settings)
            check_initialized(config)
            backend = _spawn([sys.executable, "-I", "-m", "hormuz.hosted", "--config", str(config_path), "backend"],
                             {name: settings[name] for name in SECRET_NAMES})
            _backend_ready(backend, config, stopped)
        elif mode == "provider-pilot":
            config = load_provider_profile(config_path, provider_config_path, settings)
            check_initialized(config)
            backend = _spawn([
                sys.executable, "-I", "-m", "hormuz.hosted",
                "--config", str(config_path),
                "--provider-config", str(provider_config_path),
                "provider-backend",
            ], {name: settings[name] for name in PROVIDER_CHILD_ENV_NAMES})
            _backend_ready(backend, config, stopped)
        proxy = _spawn(["/usr/bin/caddy", "run", "--config", f"/etc/hormuz/caddy/{mode}.Caddyfile", "--adapter", "caddyfile"], child_settings)
        print(json.dumps({"event": "hosted_starting", "mode": mode,
                          "inference_enabled": inference_enabled}), flush=True)
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


def backend(config, *, provider: bool = False, environ=None) -> None:
    from ._hosted_server import ProviderPilotGatewayServer, StagingGatewayServer

    with state_lock(config, exclusive=False):
        server = (
            ProviderPilotGatewayServer(config, environ=environ or {})
            if provider
            else StagingGatewayServer(config)
        )
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
    parser = argparse.ArgumentParser(description="Maintenance-first hosted authentication and provider pilot.")
    parser.add_argument("--config", type=Path, default=Path(settings["HORMUZ_CONFIG"]))
    parser.add_argument("--provider-config", type=Path, default=Path(settings[PROVIDER_CONFIG_ENV]))
    commands = parser.add_subparsers(dest="command")
    for name in (
        "serve", "backend", "provider-backend", "provider-check", "provider-bootstrap-postgres", "provider-migrate",
        "initialize", "check", "recovery-check",
    ):
        commands.add_parser(name)
    commands.add_parser("snapshot").add_argument("--output-directory", type=Path, required=True)
    commands.add_parser("migrate").add_argument("--snapshot-directory", type=Path, required=True)
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
            return supervise(settings, args.config, args.provider_config)
        if args.command == "backup-verify":
            result = verify_backup(args.archive_file, read_backup_key(args.key_file))
            print(json.dumps({"event": "hosted_operator_complete", "operation": args.command,
                              "inference_enabled": False, **result}, sort_keys=True))
            return 0
        config = (
            load_provider_profile(args.config, args.provider_config, settings)
            if args.command in {
                "provider-backend", "provider-check", "provider-bootstrap-postgres", "provider-migrate"
            }
            else load_profile(args.config, settings)
        )
        result = {}
        if args.command == "backend":
            backend(config)
        elif args.command == "provider-backend":
            backend(
                config,
                provider=True,
                environ={name: settings[name] for name in PROVIDER_CHILD_ENV_NAMES},
            )
        elif args.command == "provider-check":
            check_initialized(config)
            from .onboarding import TeamDirectory
            from .session_store import SQLiteSessionStore
            from .postgres import verify_postgres_deployment_runtime
            from .store_router import create_postgres_runtime_pool, create_usage_store

            session_settings = config.session_broker
            if session_settings.database_path is None:
                raise HostedError("hosted_provider_session_store_unavailable")
            session_store = SQLiteSessionStore(
                session_settings.database_path,
                master_key=session_settings.master_key,
                audience=session_settings.public_base_url,
                access_ttl_seconds=session_settings.access_ttl_seconds,
                absolute_ttl_seconds=session_settings.absolute_ttl_seconds,
                enrollment_ttl_seconds=session_settings.enrollment_ttl_seconds,
            )
            organization_ids = tuple(sorted(
                set(config.organization_ids).union(
                    TeamDirectory(config, session_store).managed_organization_ids()
                )
            ))
            if not organization_ids:
                raise HostedError("hosted_provider_organization_required")
            pool = create_postgres_runtime_pool(config, environ=settings)
            try:
                verify_postgres_deployment_runtime(
                    settings[PROVIDER_RUNTIME_DSN_ENV],
                    schema=config.usage_storage.postgres_schema,
                    runtime_role=config.usage_storage.postgres_runtime_role,
                    policy_control_role=config.policy_control.postgres_control_role,
                    custody_control_role=config.custody_control.postgres_control_role,
                    custody_executor_role=config.custody_executor.postgres_executor_role,
                    connection_pool=pool,
                    require_restricted_migration_login=True,
                )
                store = create_usage_store(
                    config,
                    environ=settings,
                    connection_pool=pool,
                    organization_ids=organization_ids,
                )
                store.verify_ready()
            finally:
                if pool is not None:
                    pool.close()
            result = {
                "provider_configuration_valid": True,
                "postgresql_runtime_verified": True,
                "postgresql_pool_max_connections": config.usage_storage.postgres_pool.max_connections,
            }
        elif args.command == "provider-bootstrap-postgres":
            if settings["HORMUZ_HOSTED_MODE"] != "maintenance":
                raise HostedError("hosted_provider_bootstrap_requires_maintenance")
            from .postgres import bootstrap_postgres_deployment
            from .store_router import postgres_migration_dsn

            migration_dsn = postgres_migration_dsn(config, environ=settings)
            runtime_dsn = settings[PROVIDER_RUNTIME_DSN_ENV]
            if migration_dsn in {
                settings[name]
                for name in PROVIDER_OPERATOR_SECRET_NAMES
                if name != PROVIDER_MIGRATION_DSN_ENV
            }:
                raise HostedError("hosted_provider_credentials_must_be_distinct")
            status = bootstrap_postgres_deployment(
                migration_dsn,
                runtime_dsn,
                schema=config.usage_storage.postgres_schema,
                runtime_role=config.usage_storage.postgres_runtime_role,
                policy_control_role=config.policy_control.postgres_control_role,
                custody_control_role=config.custody_control.postgres_control_role,
                custody_executor_role=config.custody_executor.postgres_executor_role,
                require_restricted_migration_login=True,
            )
            result = {
                "postgresql_schema_version": status.schema_version,
                "postgresql_schema_complete": status.schema_complete,
                "postgresql_restricted_roles": status.restricted_roles,
                "postgresql_runtime_login_restricted": status.runtime_login_restricted,
                "postgresql_runtime_membership_verified": status.runtime_membership_verified,
            }
        elif args.command == "provider-migrate":
            if settings["HORMUZ_HOSTED_MODE"] != "maintenance":
                raise HostedError("hosted_provider_migration_requires_maintenance")
            from .postgres import migrate_postgres
            from .store_router import postgres_migration_dsn

            migration_dsn = postgres_migration_dsn(config, environ=settings)
            if migration_dsn in {
                settings[name]
                for name in PROVIDER_OPERATOR_SECRET_NAMES
                if name != PROVIDER_MIGRATION_DSN_ENV
            }:
                raise HostedError("hosted_provider_credentials_must_be_distinct")
            status = migrate_postgres(
                migration_dsn,
                schema=config.usage_storage.postgres_schema,
                runtime_role=config.usage_storage.postgres_runtime_role,
                policy_control_role=config.policy_control.postgres_control_role,
                custody_control_role=config.custody_control.postgres_control_role,
                custody_executor_role=config.custody_executor.postgres_executor_role,
                require_restricted_migration_login=True,
            )
            result = {
                "postgresql_schema_version": status.version,
                "postgresql_schema_complete": status.complete,
            }
        elif args.command == "initialize":
            initialize(config)
        elif args.command == "snapshot":
            snapshot(config, args.output_directory)
        elif args.command == "migrate":
            if settings["HORMUZ_HOSTED_MODE"] != "maintenance":
                raise HostedError("hosted_migration_requires_maintenance")
            result = migrate_usage(config, args.snapshot_directory)
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
        code = (
            str(error)
            if isinstance(error, HostedError)
            else error.code
            if isinstance(error, PostgresStorageError)
            else "hosted_operation_failed"
        )
        print(json.dumps({"event": "hosted_operation_failed", "code": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
