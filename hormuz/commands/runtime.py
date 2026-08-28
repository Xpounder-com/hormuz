"""Gateway runtime, diagnostics, reporting, and storage commands."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..auth import AuthenticationError, Authenticator
from ..config import ConfigError, GatewayConfig
from ..contracts import (
    ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
    COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
    COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
    USAGE_REPORT_SCHEMA_ID,
    contract_envelope,
    contract_manifest,
)
from ..custody_runtime_projection import (
    CustodyRuntimeProjection,
    CustodyRuntimeProjectionError,
)
from ..policy_runtime import PolicyRuntime
from ..postgres import PostgresConnectionPool


@dataclass(frozen=True)
class RuntimeCommandDependencies:
    """Runtime factories resolved by :mod:`hormuz.cli` at dispatch time."""

    gateway_server: Callable[[GatewayConfig], Any]
    event_factory: Callable[[], Any]
    thread_factory: Callable[..., Any]
    signal_handler: Callable[..., Any]
    create_postgres_runtime_pool: Callable[[GatewayConfig], PostgresConnectionPool | None]
    create_usage_store: Callable[..., Any]
    policy_runtime: Callable[..., PolicyRuntime]
    custody_runtime_projection: Callable[..., CustodyRuntimeProjection]
    authenticator: Callable[[GatewayConfig], Authenticator]
    resolve_upstream_credentials: Callable[[GatewayConfig], dict[str, str]]
    migrate_postgres: Callable[..., Any]
    postgres_migration_dsn: Callable[[GatewayConfig], str]


def add_runtime_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    subparsers.add_parser("serve", help="Run the OpenAI Responses and Anthropic Messages gateway")
    subparsers.add_parser(
        "demo",
        help="Run the provider-free governed-policy quickstart on loopback",
    )
    subparsers.add_parser("doctor", help="Validate configuration and required credentials")
    contract = subparsers.add_parser("contract", help="Inspect stable Hormuz-owned contracts")
    contract_subparsers = contract.add_subparsers(dest="contract_command", required=True)
    contract_subparsers.add_parser("manifest", help="Print the stable policy and evidence schema manifest")
    status = subparsers.add_parser("status", help="Print a current-month usage and cost report")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    status.add_argument(
        "--group-by",
        choices=["organization", "team", "person", "model", "client", "provider"],
        default="person",
        help="Report dimension (default: person)",
    )
    status.add_argument("--team", help="Limit the report to a configured team ID")
    status.add_argument("--actor", help="Limit the report to a configured actor ID")


def add_storage_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    storage = subparsers.add_parser("storage", help="Verify or migrate the metadata-only usage store")
    storage_subparsers = storage.add_subparsers(dest="storage_command", required=True)
    storage_subparsers.add_parser("verify", help="Verify the configured store is safe for this binary")
    storage_subparsers.add_parser(
        "migrate",
        help="Apply bundled PostgreSQL usage-evidence migrations with the operator migration credential",
    )


def _runtime(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: RuntimeCommandDependencies,
) -> int:
    if args.command == "serve":
        return _serve(config, dependencies)
    if args.command == "doctor":
        return _doctor(config, dependencies)
    if args.command == "status":
        return _status(config, args, dependencies)
    if args.command == "storage":
        return _storage(config, args, dependencies)
    return 2


def _contract_manifest() -> int:
    print(json.dumps(contract_manifest(), indent=2, sort_keys=True))
    return 0


def _provider_free_demo() -> int:
    """Run the synthetic quickstart without loading customer configuration."""

    from ..demo import ProviderFreeDemoError, run_provider_free_demo

    try:
        result = run_provider_free_demo()
    except ProviderFreeDemoError as error:
        print(f"provider-free demo failed: {error.code}", file=sys.stderr)
        return 1
    print("Hormuz provider-free governed-policy demo")
    print("PASS allowed request reached the loopback provider simulator")
    print("PASS unapproved model was rerouted and output-capped")
    print("PASS detected secret was redacted before provider egress")
    print("PASS denied request made no provider call")
    print(
        "PASS content-free evidence validated: "
        f"{result.usage_events} usage events, {result.security_events} security event"
    )
    print(
        "PASS external provider calls: 0 "
        f"({result.provider_simulator_calls} loopback simulator calls)"
    )
    print(f"Completed in {result.elapsed_seconds:.2f} seconds; temporary evidence removed")
    return 0


def _serve(config: GatewayConfig, dependencies: RuntimeCommandDependencies) -> int:
    server = dependencies.gateway_server(config)
    missing = [protocol for protocol, value in server.upstream_credentials.items() if not value]
    if missing:
        print(
            "warning: requests for these providers will fail until credentials are set: " + ", ".join(missing),
            file=sys.stderr,
        )
    if config.ingress.mode == "external_tls_proxy":
        print(f"Hormuz private listener on http://{config.listen.host}:{config.listen.port}")
        print("Ingress: customer-controlled TLS proxy required")
    else:
        print(f"Hormuz listening on http://{config.listen.host}:{config.listen.port}")
        print("Ingress: local loopback mode")
    print("Protocols: POST /v1/responses and POST /v1/messages")
    print(f"Usage storage: {config.usage_storage.backend}")

    shutdown_started = dependencies.event_factory()

    def stop(_signum, _frame):
        """Begin an orderly drain without deadlocking the serving thread.

        ``BaseServer.shutdown`` waits for ``serve_forever`` to return. A POSIX
        signal handler executes in that same main serving thread, so calling
        it directly from here would deadlock the process rather than draining
        it. Mark readiness false immediately, then let a helper invoke the
        blocking shutdown handshake from a distinct thread.
        """

        server.begin_drain()
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        dependencies.thread_factory(
            target=server.shutdown,
            name="hormuz-sigterm-shutdown",
            daemon=True,
        ).start()

    dependencies.signal_handler(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def _doctor(config: GatewayConfig, dependencies: RuntimeCommandDependencies) -> int:
    print(f"configuration: {config.source_path}")
    print(f"listener: http://{config.listen.host}:{config.listen.port}")
    if config.ingress.mode == "external_tls_proxy":
        print(f"ingress: external TLS proxy ({len(config.ingress.trusted_proxy_cidrs)} trusted network(s))")
    else:
        print("ingress: local loopback")
    print(f"actors: {len(config.identities_by_actor)}")
    print(f"static identities: {len(config.identities_by_token)}")
    print(f"OIDC issuers: {len(config.oidc_issuers)}")
    print(f"OIDC subject mappings: {len(config.identities_by_subject)}")
    print(f"model routes: {len(config.model_routes)}")
    print(f"secret egress control: {config.secret_controls.mode}")
    print(f"usage storage: {config.usage_storage.backend}")
    runtime_pool = dependencies.create_postgres_runtime_pool(config)
    custody_runtime: CustodyRuntimeProjection | None = None
    try:
        dependencies.create_usage_store(config, connection_pool=runtime_pool)
        print("usage storage: verified")
        dependencies.policy_runtime(config, connection_pool=runtime_pool).verify_active_policies()
        print(f"policy control: {config.policy_control.mode} verified")
        custody_runtime = dependencies.custody_runtime_projection(
            config,
            connection_pool=runtime_pool,
            start_background=False,
        )
        if not custody_runtime.readiness_healthy():
            raise CustodyRuntimeProjectionError("custody_runtime_projection_unavailable")
        custody_state = "enabled" if custody_runtime.enabled else "disabled"
        print(f"custody runtime projection: {custody_state} verified")
    finally:
        if custody_runtime is not None:
            custody_runtime.close()
        _close_runtime_pool(runtime_pool)
    if config.oidc_issuers:
        try:
            metadata = dependencies.authenticator(config).validate_metadata()
        except AuthenticationError as error:
            print(f"OIDC metadata: unavailable ({error.code})")
            return 1
        print(f"OIDC signing keys: {sum(metadata.values())} usable across {len(metadata)} issuer(s)")
    credentials = dependencies.resolve_upstream_credentials(config)
    missing = [protocol for protocol, value in credentials.items() if not value]
    if missing:
        print("missing upstream credentials:")
        for protocol in missing:
            print(f"  - {protocol}")
        return 1
    print("upstream credentials: configured")
    return 0


def _status(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: RuntimeCommandDependencies,
) -> int:
    policy_runtime = dependencies.policy_runtime(config)
    rows = dependencies.create_usage_store(config).report_rows(
        group_by=args.group_by,
        actor_id=args.actor,
        team_id=args.team,
        organization_id=_required_organization(config),
    )
    report = []
    for row in rows:
        cost_usd = row["cost_microusd"] / 1_000_000
        budget_usd = _budget_for_scope(
            config,
            args.group_by,
            row,
            actor_filter=args.actor,
            team_filter=args.team,
            policy_runtime=policy_runtime,
        )
        report.append(
            {
                **row,
                "cost_usd": cost_usd,
                "budget_usd": budget_usd,
                "budget_remaining_usd": max(0.0, budget_usd - cost_usd) if budget_usd is not None else None,
                "budget_used_percent": (cost_usd / budget_usd * 100) if budget_usd else None,
            }
        )
    if args.json:
        print(
            json.dumps(
                contract_envelope(
                    USAGE_REPORT_SCHEMA_ID,
                    {
                        "month": "current",
                        "group_by": args.group_by,
                        "filters": {"actor_id": args.actor, "team_id": args.team},
                        "cost_basis": COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
                        "allocation_basis": ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
                        "coverage": COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
                        "rows": report,
                    },
                ),
                indent=2,
            )
        )
        return 0
    if not report:
        print("No Hormuz requests recorded this month.")
        return 0
    print(
        "SCOPE_ID\tSCOPE_NAME\tTEAM\tPROVIDER\tCLIENT\tREQUESTS\tSUCCEEDED\tFAILED\tDENIED\tRATE_LIMITED\t"
        "INPUT\tOUTPUT\tCACHE_READ\tCACHE_WRITE\tREASONING\tTOTAL\tCOST_USD\tBUDGET_USD\t"
        "REMAINING_USD\tBUDGET_USED_PCT\tACTORS\tREDACTIONS"
    )
    for row in report:
        print(
            f"{row['scope_id']}\t{row['scope_name']}\t{row.get('team_name', '-')}\t"
            f"{row.get('protocol', '-')}\t{row.get('client', '-')}\t{row['requests']}\t"
            f"{row['succeeded']}\t{row['failed']}\t{row['denied']}\t{row['rate_limited']}\t{row['input_tokens']}\t"
            f"{row['output_tokens']}\t{row['cache_read_tokens']}\t{row['cache_write_tokens']}\t"
            f"{row['reasoning_tokens']}\t{row['total_tokens']}\t{row['cost_usd']:.6f}\t"
            f"{_display_number(row['budget_usd'])}\t{_display_number(row['budget_remaining_usd'])}\t"
            f"{_display_number(row['budget_used_percent'])}\t{row['active_actors']}\t{row['redactions']}"
        )
    return 0


def _budget_for_scope(
    config: GatewayConfig,
    group_by: str,
    row: dict[str, object],
    *,
    actor_filter: str | None = None,
    team_filter: str | None = None,
    policy_runtime: PolicyRuntime | None = None,
    policy_runtime_factory: Callable[..., PolicyRuntime] = PolicyRuntime,
) -> float | None:
    if config.policy_control.mode == "postgresql":
        return _managed_budget_for_scope(
            config,
            group_by,
            row,
            actor_filter=actor_filter,
            team_filter=team_filter,
            policy_runtime=policy_runtime or policy_runtime_factory(config),
        )
    scope_id = str(row["scope_id"])
    if group_by == "organization":
        if actor_filter is not None or team_filter is not None:
            return None
        return config.organization_policy.monthly_budget_usd
    if group_by == "team":
        if actor_filter is not None:
            return None
        policy = config.team_policies.get(scope_id)
        return policy.monthly_budget_usd if policy is not None else None
    if group_by != "person":
        return None

    identity = config.identities_by_actor.get(scope_id)
    if identity is None:
        return None
    caps = [
        policy.per_actor_monthly_budget_usd
        for policy in (
            config.organization_policy,
            config.team_policies.get(identity.team_id),
            config.actor_policies.get(identity.actor_id),
        )
        if policy is not None and policy.per_actor_monthly_budget_usd is not None
    ]
    actor_policy = config.actor_policies.get(identity.actor_id)
    if actor_policy is not None and actor_policy.monthly_budget_usd is not None:
        caps.append(actor_policy.monthly_budget_usd)
    return min(caps) if caps else None


def _managed_budget_for_scope(
    config: GatewayConfig,
    group_by: str,
    row: dict[str, object],
    *,
    actor_filter: str | None,
    team_filter: str | None,
    policy_runtime: PolicyRuntime,
) -> float | None:
    """Report current budget metadata from the active immutable policy.

    Historical request evidence remains pinned to its recorded policy version;
    the status budget column is intentionally the currently active cap, just
    as it was for configuration-backed local mode.
    """

    scope_id = str(row["scope_id"])
    if group_by == "organization":
        if actor_filter is not None or team_filter is not None:
            return None
        identity = next(
            (item for item in config.identities_by_actor.values() if item.organization_id == scope_id),
            None,
        )
        return (
            policy_runtime.snapshot_for(identity).organization_policy.monthly_budget_usd
            if identity is not None
            else None
        )
    if group_by == "team":
        if actor_filter is not None:
            return None
        identity = next(
            (item for item in config.identities_by_actor.values() if item.team_id == scope_id),
            None,
        )
        if identity is None:
            return None
        team_policy = policy_runtime.snapshot_for(identity).team_policy
        return team_policy.monthly_budget_usd if team_policy is not None else None
    if group_by != "person":
        return None
    identity = config.identities_by_actor.get(scope_id)
    if identity is None:
        return None
    snapshot = policy_runtime.snapshot_for(identity)
    caps = [
        policy.per_actor_monthly_budget_usd
        for policy in (snapshot.organization_policy, snapshot.team_policy, snapshot.actor_policy)
        if policy is not None and policy.per_actor_monthly_budget_usd is not None
    ]
    if snapshot.actor_policy is not None and snapshot.actor_policy.monthly_budget_usd is not None:
        caps.append(snapshot.actor_policy.monthly_budget_usd)
    return min(caps) if caps else None


def _display_number(value: object) -> str:
    return "-" if value is None else f"{float(value):.2f}"


def _required_organization(config: GatewayConfig) -> str:
    organization_ids = config.organization_ids
    if len(organization_ids) != 1:
        raise ConfigError(
            "usage reporting, audit export, and encrypted custody commands require exactly one configured organization; "
            "use a tenant-scoped configuration"
        )
    return organization_ids[0]


def _storage(
    config: GatewayConfig,
    args: argparse.Namespace,
    dependencies: RuntimeCommandDependencies,
) -> int:
    if args.storage_command == "verify":
        runtime_pool = dependencies.create_postgres_runtime_pool(config)
        try:
            dependencies.create_usage_store(config, connection_pool=runtime_pool)
            print(f"usage storage verified: {config.usage_storage.backend}")
        finally:
            _close_runtime_pool(runtime_pool)
        return 0
    if args.storage_command == "migrate":
        if config.usage_storage.backend == "sqlite":
            dependencies.create_usage_store(config)
            print("SQLite usage storage migration is current")
            return 0
        status = dependencies.migrate_postgres(
            dependencies.postgres_migration_dsn(config),
            schema=config.usage_storage.postgres_schema,
            runtime_role=config.usage_storage.postgres_runtime_role,
            policy_control_role=config.policy_control.postgres_control_role,
            custody_control_role=config.custody_control.postgres_control_role,
            custody_executor_role=config.custody_executor.postgres_executor_role,
        )
        print(f"PostgreSQL usage storage migration is current: v{status.version}")
        return 0
    raise ConfigError("unsupported storage command")


def _close_runtime_pool(pool: PostgresConnectionPool | None) -> None:
    """Close a one-shot diagnostic pool after its final verification query."""

    if pool is not None:
        pool.close()
