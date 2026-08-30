from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import signal
import sys
import threading
from pathlib import Path

from .audit_chain import AuditChainError
from .auth import Authenticator
from .commands import audit as audit_commands
from .commands import client as client_commands
from .commands import custody as custody_commands
from .commands import policy as policy_commands
from .commands import portfolio as portfolio_commands
from .commands import runtime as runtime_commands
from .config import ConfigError, GatewayConfig
from .custody import CustodyError
from .custody_runtime import (
    create_audit_anchor_sink,
    create_data_key_provider,
    read_envelope_file,
    resolve_upstream_credentials,
    write_envelope_file,
)
from .custody_runtime_projection import (
    CustodyRuntimeProjection,
    CustodyRuntimeProjectionError,
)
from .evidence import EvidenceStorageError
from .custody_control import CustodyControlService
from .custody_executor import CustodyExecutorService
from .custody_execution_repository import CustodyExecutionError
from .custody_lifecycle import CustodyLifecycleError
from .custody_repository import CustodyControlError
from .policy_analysis import PolicyAnalysisError
from .policy_control import PolicyControlService
from .policy_document import PolicyDocument, PolicyDocumentError
from .policy_repository import PolicyControlError
from .policy_runtime import PolicyRuntime
from .policy_scenarios import PolicyScenarioError
from .postgres import PostgresConnectionPool, PostgresStorageError, migrate_postgres
from .server import GatewayServer
from .store import StorageSchemaError
from .store_router import create_postgres_runtime_pool, create_usage_store, postgres_migration_dsn


_DEPRECATED_CONTEXT_COMMANDS = frozenset({"context-pack"})
_CONTEXT_EXPERIMENT_MOVED_ERROR = "context_experiment_moved"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hormuz",
        description="Enterprise AI policy and usage control for Codex and Claude Code.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("HORMUZ_CONFIG", "hormuz.json"),
        help="Path to Hormuz JSON configuration (default: hormuz.json or HORMUZ_CONFIG)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable request-boundary logs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    runtime_commands.add_runtime_commands(subparsers)

    policy_commands.add_policy_commands(subparsers)
    portfolio_commands.add_portfolio_commands(subparsers)

    client_commands.add_client_commands(subparsers)

    audit_commands.add_audit_commands(subparsers)

    custody_commands.add_custody_commands(subparsers)

    runtime_commands.add_storage_commands(subparsers)

    return parser










def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if _is_deprecated_context_command(raw_argv):
        return _context_experiment_moved()
    args = build_parser().parse_args(_normalize_command_argv(raw_argv))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING if args.command == "demo" else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.command == "auth" and args.auth_command == "token":
        return client_commands._auth_token(args.env)
    if args.command == "contract" and args.contract_command == "manifest":
        return runtime_commands._contract_manifest()
    if args.command == "demo":
        return runtime_commands._provider_free_demo()
    if args.command == "policy" and args.policy_control_command == "templates":
        return policy_commands._policy_template_catalog()
    try:
        if args.command == "policy" and args.policy_control_command == "demo":
            return policy_commands._policy_demo(args, _policy_command_dependencies())
        if args.command == "policy" and args.policy_control_command == "scenarios":
            return policy_commands._policy_scenarios(args)
        if args.command == "policy" and args.policy_control_command in {"create", "validate"}:
            context = GatewayConfig.load_policy_validation_context(args.config)
            if args.policy_control_command == "create":
                return policy_commands._policy_create(context, args, _policy_command_dependencies())
            return policy_commands._policy_validate(context, args.file)
        if (
            args.command == "policy"
            and args.policy_control_command in {"compare", "preview", "evaluate"}
            and policy_commands._policy_analysis_requests_local_documents(args)
        ):
            analysis_context = GatewayConfig.load_policy_analysis_context(args.config)
            if analysis_context.usage_storage.backend == "sqlite":
                return policy_commands._policy_analysis(
                    analysis_context, args, _policy_command_dependencies()
                )
        config = GatewayConfig.load(args.config)
        if args.command == "portfolio":
            return portfolio_commands.run(config, args)
        if args.command in {"serve", "doctor", "status", "storage"}:
            return runtime_commands._runtime(config, args, _runtime_command_dependencies())
        if args.command == "policy" and args.policy_control_command == "check":
            return policy_commands._policy_check(config, args, _policy_command_dependencies())
        if args.command == "policy":
            return policy_commands._policy_control(config, args, _policy_command_dependencies())
        if args.command == "client":
            return client_commands._client(config, args)
        if args.command == "audit":
            return audit_commands._audit(config, args, _audit_command_dependencies())
        if args.command == "custody":
            return custody_commands._custody(config, args, _custody_command_dependencies())
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except (EvidenceStorageError, PostgresStorageError, StorageSchemaError, AuditChainError) as error:
        print(f"storage error: {error.code}", file=sys.stderr)
        return 2
    except CustodyError as error:
        print(f"custody error: {error.code}", file=sys.stderr)
        return 2
    except CustodyControlError as error:
        print(f"custody control error: {error.code}", file=sys.stderr)
        return 2
    except CustodyRuntimeProjectionError as error:
        print(f"custody runtime error: {error.code}", file=sys.stderr)
        return 2
    except (CustodyExecutionError, CustodyLifecycleError) as error:
        print(f"custody executor error: {error.code}", file=sys.stderr)
        return 2
    except PolicyDocumentError as error:
        policy_commands._print_policy_document_failure(error.code, error.reason, hint=error.hint)
        return 2
    except PolicyControlError as error:
        print(f"policy control error: {error.code}", file=sys.stderr)
        return 2
    except PolicyAnalysisError as error:
        print(f"policy analysis error: {error.code}", file=sys.stderr)
        return 2
    except PolicyScenarioError as error:
        policy_commands._print_policy_scenario_failure(error.code, error.reason, hint=error.hint)
        return 2
    except policy_commands.PolicyDemoError as error:
        policy_commands._print_policy_demo_failure(error.code, error.reason, hint=error.hint)
        return 2
    except (OSError, sqlite3.Error):
        print("storage error: storage_unavailable", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 2


def _policy_command_dependencies() -> policy_commands.PolicyCommandDependencies:
    """Resolve CLI patch points at dispatch time without mutable command globals."""

    return policy_commands.PolicyCommandDependencies(
        policy_control_service=PolicyControlService,
        create_usage_store=create_usage_store,
        write_policy_document=_write_policy_document,
    )


def _runtime_command_dependencies() -> runtime_commands.RuntimeCommandDependencies:
    """Resolve runtime patch points at dispatch time."""

    return runtime_commands.RuntimeCommandDependencies(
        gateway_server=GatewayServer,
        event_factory=threading.Event,
        thread_factory=threading.Thread,
        signal_handler=signal.signal,
        create_postgres_runtime_pool=create_postgres_runtime_pool,
        create_usage_store=create_usage_store,
        policy_runtime=PolicyRuntime,
        custody_runtime_projection=CustodyRuntimeProjection,
        authenticator=Authenticator,
        resolve_upstream_credentials=resolve_upstream_credentials,
        migrate_postgres=migrate_postgres,
        postgres_migration_dsn=postgres_migration_dsn,
    )


def _custody_command_dependencies() -> custody_commands.CustodyCommandDependencies:
    """Resolve CLI patch points at dispatch time without mutable command globals."""

    return custody_commands.CustodyCommandDependencies(
        custody_control_service=CustodyControlService,
        custody_executor_service=CustodyExecutorService,
        create_audit_anchor_sink=create_audit_anchor_sink,
        create_data_key_provider=create_data_key_provider,
        read_envelope_file=read_envelope_file,
        write_envelope_file=write_envelope_file,
        required_organization=_required_organization,
    )


def _custody_verify(config: GatewayConfig) -> int:
    """Compatibility seam for existing CLI callers and tests."""

    return custody_commands._custody_verify(config, _custody_command_dependencies())


def _write_policy_document(path: Path, document: PolicyDocument, *, force: bool) -> None:
    """Compatibility seam for existing CLI callers and tests."""

    policy_commands._write_policy_document(path, document, force=force)


def _normalize_command_argv(argv: list[str]) -> list[str]:
    """Map legacy hyphenated command tokens onto the primary spaced tree."""

    index = _top_level_command_index(argv)
    if index is None:
        return list(argv)
    prefix = list(argv[:index])
    command = list(argv[index:])
    top_level_aliases = {
        "contract-manifest": ["contract", "manifest"],
        "policy-check": ["policy", "check"],
        "client-config": ["client", "config"],
        "audit-export": ["audit", "export"],
        "audit-anchor": ["audit", "anchor"],
        "audit-chain": ["audit", "chain"],
        "custody-executor": ["custody", "executor"],
    }
    replacement = top_level_aliases.get(command[0])
    if replacement is not None:
        command = [*replacement, *command[1:]]
    nested_aliases = (
        (("policy", "break-glass", "recover"), ("policy", "recover")),
        (("policy", "administrator", "revoke-static"), ("policy", "administrator", "retire", "static")),
        (("custody", "administrator", "revoke-static"), ("custody", "administrator", "retire", "static")),
        (("custody", "evidence", "deletion-check"), ("custody", "evidence", "deletion", "check")),
        (("custody", "executor", "register-assets"), ("custody", "executor", "register", "assets")),
    )
    for legacy, primary in nested_aliases:
        if tuple(command[: len(legacy)]) == legacy:
            command = [*primary, *command[len(legacy) :]]
            break
    return [*prefix, *command]


def _top_level_command_index(argv: list[str]) -> int | None:
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--":
            return None
        if value == "--config":
            index += 2
            continue
        if value.startswith("--config=") or value == "--verbose":
            index += 1
            continue
        if value.startswith("-"):
            index += 1
            continue
        return index
    return None


def _is_deprecated_context_command(argv: list[str]) -> bool:
    """Identify the former core command without registering it with argparse."""

    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--":
            return False
        if value == "--config":
            index += 2
            continue
        if value.startswith("--config=") or value == "--verbose":
            index += 1
            continue
        if value.startswith("-"):
            index += 1
            continue
        return value in _DEPRECATED_CONTEXT_COMMANDS
    return False


def _context_experiment_moved() -> int:
    print(
        "error [context_experiment_moved]: `hormuz context-pack` is no longer part of the core gateway. "
        "Install `hormuz-context-experiment` and run `hormuz-context-experiment ... context-pack ...`; "
        "see docs/CONTEXT_EXPERIMENT_MIGRATION.md.",
        file=sys.stderr,
    )
    return 2


















def _provider_free_demo() -> int:
    return runtime_commands._provider_free_demo()


def _serve(config: GatewayConfig) -> int:
    return runtime_commands._serve(config, _runtime_command_dependencies())


def _doctor(config: GatewayConfig) -> int:
    return runtime_commands._doctor(config, _runtime_command_dependencies())


def _status(config: GatewayConfig, args: argparse.Namespace) -> int:
    return runtime_commands._status(config, args, _runtime_command_dependencies())


def _budget_for_scope(
    config: GatewayConfig,
    group_by: str,
    row: dict[str, object],
    *,
    actor_filter: str | None = None,
    team_filter: str | None = None,
    policy_runtime: PolicyRuntime | None = None,
) -> float | None:
    return runtime_commands._budget_for_scope(
        config,
        group_by,
        row,
        actor_filter=actor_filter,
        team_filter=team_filter,
        policy_runtime=policy_runtime,
        policy_runtime_factory=PolicyRuntime,
    )


def _managed_budget_for_scope(
    config: GatewayConfig,
    group_by: str,
    row: dict[str, object],
    *,
    actor_filter: str | None,
    team_filter: str | None,
    policy_runtime: PolicyRuntime,
) -> float | None:
    return runtime_commands._managed_budget_for_scope(
        config,
        group_by,
        row,
        actor_filter=actor_filter,
        team_filter=team_filter,
        policy_runtime=policy_runtime,
    )


def _display_number(value: object) -> str:
    return runtime_commands._display_number(value)


def _client_config(
    config: GatewayConfig,
    client: str,
    url: str | None,
    *,
    actor_id: str | None = None,
    auth_mode: str = "auto",
    credential_env: str | None = None,
) -> int:
    return client_commands._client_config(
        config,
        client,
        url,
        actor_id=actor_id,
        auth_mode=auth_mode,
        credential_env=credential_env,
    )


def _auth_token(env_name: str) -> int:
    return client_commands._auth_token(env_name)


def _client_base_url(value: str) -> str:
    return client_commands._client_base_url(value)


def _audit_command_dependencies() -> audit_commands.AuditCommandDependencies:
    return audit_commands.AuditCommandDependencies(
        create_usage_store=create_usage_store,
        create_audit_anchor_sink=create_audit_anchor_sink,
        required_organization=_required_organization,
    )


def _audit_export(config: GatewayConfig, args: argparse.Namespace) -> int:
    return audit_commands._audit_export(config, args, _audit_command_dependencies())


def _audit_anchor(config: GatewayConfig, args: argparse.Namespace) -> int:
    return audit_commands._audit_anchor(config, args, _audit_command_dependencies())


def _audit_chain(config: GatewayConfig, args: argparse.Namespace) -> int:
    return audit_commands._audit_chain(config, args, _audit_command_dependencies())


def _write_audit_chain_checkpoint(path: Path, serialized: bytes, *, force: bool) -> None:
    audit_commands._write_audit_chain_checkpoint(path, serialized, force=force, write=os.write)


def _read_audit_chain_checkpoint(path: Path) -> dict[str, object]:
    return audit_commands._read_audit_chain_checkpoint(path)


def _is_sha256_digest(value: object) -> bool:
    return audit_commands._is_sha256_digest(value)


def _audit_since(value: str | None) -> str:
    return audit_commands._audit_since(value)


def _required_organization(config: GatewayConfig) -> str:
    return runtime_commands._required_organization(config)


def _storage(config: GatewayConfig, args: argparse.Namespace) -> int:
    return runtime_commands._storage(config, args, _runtime_command_dependencies())


def _close_runtime_pool(pool: PostgresConnectionPool | None) -> None:
    runtime_commands._close_runtime_pool(pool)


if __name__ == "__main__":
    raise SystemExit(main())
