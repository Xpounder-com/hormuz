from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
import sqlite3
import shlex
import signal
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from .auth import AuthenticationError, Authenticator
from .audit_chain import (
    AuditChainError,
    audit_chain_checkpoint_summary,
    build_audit_chain_checkpoint,
    parse_audit_chain_checkpoint,
    serialize_audit_chain_checkpoint,
)
from .config import ConfigError, GatewayConfig
from .custody import (
    KEY_PURPOSES,
    CustodyError,
    EnvelopeCipher,
    audit_anchor_summary,
    build_audit_anchor_artifact,
    serialize_envelope,
    serialize_audit_anchor_artifact,
)
from .custody_runtime import (
    create_audit_anchor_sink,
    create_data_key_provider,
    read_envelope_file,
    resolve_upstream_credentials,
    write_envelope_file,
)
from .evidence import EvidenceStorageError
from .contracts import (
    ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
    COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
    COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
    POLICY_CONTROL_STATUS_SCHEMA_ID,
    CUSTODY_CONTROL_STATUS_SCHEMA_ID,
    POLICY_DECISION_SCHEMA_ID,
    USAGE_REPORT_SCHEMA_ID,
    contract_envelope,
    contract_manifest,
)
from .policy import PolicyEngine
from .custody_control import CustodyControlService
from .custody_repository import (
    CUSTODY_OPERATIONS,
    CustodyControlError,
    CustodyControlStatus,
    CustodyOperationIntent,
)
from .policy_control import PolicyControlService
from .policy_document import PolicyDocumentError
from .policy_repository import PolicyActivation, PolicyControlError, PolicyControlStatus, PolicyVersionRecord
from .policy_runtime import PolicyRuntime
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

    subparsers.add_parser("serve", help="Run the OpenAI Responses and Anthropic Messages gateway")
    subparsers.add_parser("doctor", help="Validate configuration and required credentials")
    subparsers.add_parser("contract-manifest", help="Print the stable policy and evidence schema manifest")
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

    policy = subparsers.add_parser("policy-check", help="Evaluate a request without sending it upstream")
    policy.add_argument("--actor", required=True, help="Configured actor ID")
    policy.add_argument("--client", required=True, choices=["codex", "claude-code"])
    policy.add_argument("--protocol", required=True, choices=["openai", "anthropic"])
    policy.add_argument("--model", required=True, help="Company model alias")
    policy.add_argument("--max-output-tokens", type=int)

    policy_control = subparsers.add_parser(
        "policy",
        help="Bootstrap and administer immutable tenant policy versions",
    )
    policy_control_subparsers = policy_control.add_subparsers(dest="policy_control_command", required=True)

    bootstrap = policy_control_subparsers.add_parser(
        "bootstrap",
        help="Persist one-time configuration-seeded policy administrators",
    )
    _policy_control_auth_arguments(bootstrap)

    stage = policy_control_subparsers.add_parser("stage", help="Validate and stage an immutable policy document")
    _policy_control_auth_arguments(stage)
    stage.add_argument("--file", required=True, help="Policy-document JSON path")

    activate = policy_control_subparsers.add_parser("activate", help="Atomically activate a staged policy version")
    _policy_control_auth_arguments(activate)
    activate.add_argument("--version", required=True, help="Immutable sha256 policy version")

    rollback = policy_control_subparsers.add_parser("rollback", help="Reactivate a previously active policy version")
    _policy_control_auth_arguments(rollback)
    rollback.add_argument("--version", required=True, help="Previously active sha256 policy version")

    policy_status = policy_control_subparsers.add_parser("status", help="Show tenant policy-control metadata")
    _policy_control_auth_arguments(policy_status)
    policy_status.add_argument("--json", action="store_true", help="Emit machine-readable metadata-only JSON")

    administrator = policy_control_subparsers.add_parser("administrator", help="Manage governed policy administrators")
    administrator_subparsers = administrator.add_subparsers(dest="policy_administrator_command", required=True)
    for action in ("grant", "revoke"):
        command = administrator_subparsers.add_parser(action, help=f"{action.title()} an OIDC policy administrator")
        _policy_control_auth_arguments(command)
        command.add_argument("--issuer", required=True, help="Configured OIDC issuer URL")
        command.add_argument("--subject", required=True, help="Stable OIDC subject")
    revoke_static = administrator_subparsers.add_parser(
        "revoke-static",
        help="Retire a persisted static bootstrap policy administrator",
    )
    _policy_control_auth_arguments(revoke_static)
    revoke_static.add_argument("--actor-id", required=True, help="Persisted static bootstrap actor ID")

    break_glass = policy_control_subparsers.add_parser(
        "break-glass",
        help="Recover OIDC policy authority only after every administrator is lost",
    )
    break_glass_subparsers = break_glass.add_subparsers(dest="policy_break_glass_command", required=True)
    recover = break_glass_subparsers.add_parser("recover", help="Recover one OIDC administrator under break-glass controls")
    recover.add_argument("--organization", required=True, help="Tenant organization ID")
    recover.add_argument("--issuer", required=True, help="Configured OIDC issuer URL")
    recover.add_argument("--subject", required=True, help="Stable OIDC subject")
    recover.add_argument(
        "--reason-code",
        required=True,
        choices=["all_administrators_lost", "administrator_store_recovered"],
        help="Controlled recovery reason",
    )

    connect = subparsers.add_parser("client-config", help="Print client configuration for this gateway")
    connect.add_argument("client", choices=["codex", "claude"])
    connect.add_argument("--url", help="Externally reachable gateway URL; defaults to configured listener")
    connect.add_argument("--actor", help="Configured actor ID; defaults to the first configured actor")
    connect.add_argument(
        "--auth-mode",
        choices=["auto", "static", "oidc"],
        default="auto",
        help="Credential source to configure (default: static when available, otherwise OIDC)",
    )
    connect.add_argument(
        "--credential-env",
        help="Environment variable containing the credential (OIDC default: HORMUZ_OIDC_ACCESS_TOKEN)",
    )

    auth = subparsers.add_parser("auth", help="Credential helpers for AI clients")
    auth_subparsers = auth.add_subparsers(dest="auth_command", required=True)
    auth_token = auth_subparsers.add_parser("token", help="Print a credential from an environment variable")
    auth_token.add_argument("--env", default="HORMUZ_OIDC_ACCESS_TOKEN", help="Credential environment variable")

    audit = subparsers.add_parser("audit-export", help="Export metadata-only usage and security events as JSONL")
    audit.add_argument("--kind", choices=["all", "usage", "security"], default="all")
    audit.add_argument("--since", help="UTC ISO-8601 lower bound (default: start of current month)")
    audit.add_argument("--output", default="-", help="Output path or - for stdout (default: -)")
    audit.add_argument("--force", action="store_true", help="Allow replacing an existing output file")

    audit_anchor = subparsers.add_parser(
        "audit-anchor",
        help="Export and immutably retain a metadata-only audit snapshot",
    )
    audit_anchor.add_argument("--kind", choices=["all", "usage", "security"], default="all")
    audit_anchor.add_argument("--since", help="UTC ISO-8601 lower bound (default: start of current month)")

    audit_chain = subparsers.add_parser(
        "audit-chain",
        help="Operate the per-organization commit-time audit chain",
    )
    audit_chain_subparsers = audit_chain.add_subparsers(dest="audit_chain_command", required=True)
    audit_chain_subparsers.add_parser(
        "status",
        help="Show local chain and checkpoint freshness without contacting Object Lock",
    )
    audit_chain_anchor = audit_chain_subparsers.add_parser(
        "anchor",
        help="Externally retain the current chain checkpoint outside the request path",
    )
    audit_chain_anchor.add_argument(
        "--output",
        required=True,
        help="Write the canonical metadata-only checkpoint artifact to this path",
    )
    audit_chain_anchor.add_argument("--force", action="store_true", help="Allow replacing an existing checkpoint path")
    audit_chain_verify = audit_chain_subparsers.add_parser(
        "verify",
        help="Verify chain order, event correspondence, and an external checkpoint",
    )
    audit_chain_verify.add_argument("--checkpoint", required=True, help="Canonical externally retained checkpoint artifact")
    audit_chain_epoch = audit_chain_subparsers.add_parser(
        "epoch",
        help="Explicitly begin a restore or migration epoch from a trusted checkpoint",
    )
    audit_chain_epoch.add_argument("--checkpoint", required=True, help="Trusted canonical checkpoint artifact")
    audit_chain_epoch.add_argument("--reason", required=True, choices=["restore", "migration"])
    audit_chain_epoch.add_argument(
        "--confirm",
        required=True,
        help="Type START_NEW_AUDIT_CHAIN_EPOCH to confirm this controlled recovery action",
    )

    custody = subparsers.add_parser("custody", help="Operate configured encrypted credential custody")
    custody_subparsers = custody.add_subparsers(dest="custody_command", required=True)
    custody_subparsers.add_parser(
        "verify",
        help="Exercise configured key custody and verify Object Lock readiness without writing an audit object",
    )
    seal = custody_subparsers.add_parser(
        "seal",
        help="Seal a value from an environment variable into an owner-only encrypted envelope",
    )
    seal.add_argument("--purpose", choices=sorted(KEY_PURPOSES), required=True)
    seal.add_argument("--input-env", required=True, help="Environment variable containing the value to seal")
    seal.add_argument("--output", required=True, help="Owner-only encrypted envelope output path")
    seal.add_argument("--force", action="store_true", help="Allow replacing an existing envelope path")
    rewrap = custody_subparsers.add_parser(
        "rewrap",
        help="Move an encrypted data key to the current key for its existing purpose",
    )
    rewrap.add_argument("--input", required=True, help="Existing encrypted envelope path")
    rewrap.add_argument("--output", required=True, help="Owner-only rewrapped envelope output path")
    rewrap.add_argument("--force", action="store_true", help="Allow replacing an existing envelope path")

    custody_bootstrap = custody_subparsers.add_parser(
        "bootstrap",
        help="Persist one-time configuration-seeded custody administrators",
    )
    _custody_control_auth_arguments(custody_bootstrap)

    custody_status = custody_subparsers.add_parser(
        "status",
        help="Show tenant custody authorities and content-free approval intents",
    )
    _custody_control_auth_arguments(custody_status)
    custody_status.add_argument("--json", action="store_true", help="Emit machine-readable metadata-only JSON")

    custody_administrator = custody_subparsers.add_parser(
        "administrator",
        help="Manage governed custody administrators",
    )
    custody_administrator_subparsers = custody_administrator.add_subparsers(
        dest="custody_administrator_command",
        required=True,
    )
    for action in ("grant", "revoke"):
        command = custody_administrator_subparsers.add_parser(
            action,
            help=f"{action.title()} an OIDC custody administrator",
        )
        _custody_control_auth_arguments(command)
        command.add_argument("--issuer", required=True, help="Configured OIDC issuer URL")
        command.add_argument("--subject", required=True, help="Stable OIDC subject")
    custody_revoke_static = custody_administrator_subparsers.add_parser(
        "revoke-static",
        help="Retire a persisted static bootstrap custody administrator",
    )
    _custody_control_auth_arguments(custody_revoke_static)
    custody_revoke_static.add_argument("--actor-id", required=True, help="Persisted static bootstrap actor ID")

    authorize = custody_subparsers.add_parser(
        "authorize",
        help="Create an exact content-free custody-operation intent and record the first approval",
    )
    _custody_control_auth_arguments(authorize)
    authorize.add_argument("--operation", required=True, choices=sorted(CUSTODY_OPERATIONS))
    authorize.add_argument("--target-sha256", required=True, help="Digest of the exact lifecycle target")
    authorize.add_argument("--parameters-sha256", required=True, help="Digest of the normalized execution plan")
    authorize.add_argument(
        "--protected-input-ref-sha256",
        help="Digest of a protected input handle; required only for initial envelope sealing",
    )

    approve = custody_subparsers.add_parser(
        "approve",
        help="Add the distinct second administrator approval required by a destructive operation",
    )
    _custody_control_auth_arguments(approve)
    approve.add_argument("--operation-id", required=True, help="Immutable custody operation identifier")

    storage = subparsers.add_parser("storage", help="Verify or migrate the metadata-only usage store")
    storage_subparsers = storage.add_subparsers(dest="storage_command", required=True)
    storage_subparsers.add_parser("verify", help="Verify the configured store is safe for this binary")
    storage_subparsers.add_parser(
        "migrate",
        help="Apply bundled PostgreSQL usage-evidence migrations with the operator migration credential",
    )

    return parser


def _policy_control_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--organization", required=True, help="Tenant organization ID")
    parser.add_argument(
        "--credential-env",
        default="HORMUZ_POLICY_ADMIN_TOKEN",
        help="Environment variable holding an authenticated policy-admin credential",
    )


def _custody_control_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--organization", required=True, help="Tenant organization ID")
    parser.add_argument(
        "--credential-env",
        default="HORMUZ_CUSTODY_ADMIN_TOKEN",
        help="Environment variable holding an authenticated custody-admin credential",
    )


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if _is_deprecated_context_command(raw_argv):
        return _context_experiment_moved()
    args = build_parser().parse_args(raw_argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.command == "auth" and args.auth_command == "token":
        return _auth_token(args.env)
    if args.command == "contract-manifest":
        print(json.dumps(contract_manifest(), indent=2, sort_keys=True))
        return 0
    try:
        config = GatewayConfig.load(args.config)
        if args.command == "serve":
            return _serve(config)
        if args.command == "doctor":
            return _doctor(config)
        if args.command == "status":
            return _status(config, args)
        if args.command == "policy-check":
            return _policy_check(config, args)
        if args.command == "policy":
            return _policy_control(config, args)
        if args.command == "client-config":
            return _client_config(
                config,
                args.client,
                args.url,
                actor_id=args.actor,
                auth_mode=args.auth_mode,
                credential_env=args.credential_env,
            )
        if args.command == "audit-export":
            return _audit_export(config, args)
        if args.command == "audit-anchor":
            return _audit_anchor(config, args)
        if args.command == "audit-chain":
            return _audit_chain(config, args)
        if args.command == "custody":
            return _custody(config, args)
        if args.command == "storage":
            return _storage(config, args)
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
    except (PolicyControlError, PolicyDocumentError) as error:
        print(f"policy control error: {error.code}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("storage error: storage_unavailable", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 2


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


def _serve(config: GatewayConfig) -> int:
    server = GatewayServer(config)
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

    shutdown_started = threading.Event()

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
        threading.Thread(target=server.shutdown, name="hormuz-sigterm-shutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def _doctor(config: GatewayConfig) -> int:
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
    runtime_pool = create_postgres_runtime_pool(config)
    try:
        create_usage_store(config, connection_pool=runtime_pool)
        print("usage storage: verified")
        PolicyRuntime(config, connection_pool=runtime_pool).verify_active_policies()
        print(f"policy control: {config.policy_control.mode} verified")
        if config.custody_control.mode == "postgresql":
            CustodyControlService(config)
        print(f"custody control: {config.custody_control.mode} verified")
    finally:
        _close_runtime_pool(runtime_pool)
    if config.oidc_issuers:
        try:
            metadata = Authenticator(config).validate_metadata()
        except AuthenticationError as error:
            print(f"OIDC metadata: unavailable ({error.code})")
            return 1
        print(f"OIDC signing keys: {sum(metadata.values())} usable across {len(metadata)} issuer(s)")
    credentials = resolve_upstream_credentials(config)
    missing = [protocol for protocol, value in credentials.items() if not value]
    if missing:
        print("missing upstream credentials:")
        for protocol in missing:
            print(f"  - {protocol}")
        return 1
    print("upstream credentials: configured")
    return 0


def _status(config: GatewayConfig, args: argparse.Namespace) -> int:
    policy_runtime = PolicyRuntime(config)
    rows = create_usage_store(config).report_rows(
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
) -> float | None:
    if config.policy_control.mode == "postgresql":
        return _managed_budget_for_scope(
            config,
            group_by,
            row,
            actor_filter=actor_filter,
            team_filter=team_filter,
            policy_runtime=policy_runtime or PolicyRuntime(config),
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


def _policy_check(config: GatewayConfig, args: argparse.Namespace) -> int:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        print(f"unknown actor: {args.actor}", file=sys.stderr)
        return 2
    store = create_usage_store(config)
    decision = PolicyEngine(config, store).evaluate(
        identity=identity,
        client=args.client,
        protocol=args.protocol,
        requested_model=args.model,
        requested_output_tokens=args.max_output_tokens,
    )
    print(
        json.dumps(
            contract_envelope(
                POLICY_DECISION_SCHEMA_ID,
                {
                    "allowed": decision.allowed,
                    "action": decision.action,
                    "reason": decision.reason,
                    "requested_model": decision.requested_model,
                    "resolved_alias": decision.resolved_alias,
                    "routed_model": decision.route.upstream_model if decision.route else None,
                    "max_output_tokens": decision.max_output_tokens,
                    "policy_version": decision.policy_version,
                },
            ),
            indent=2,
        )
    )
    return 0 if decision.allowed else 3


def _policy_control(config: GatewayConfig, args: argparse.Namespace) -> int:
    """Run a CLI command through the authenticated policy-control service."""

    service = PolicyControlService(config)
    command = args.policy_control_command
    if command == "bootstrap":
        administrators = service.bootstrap(
            organization_id=args.organization,
            credential_env=args.credential_env,
        )
        print(f"policy bootstrap initialized: organization={args.organization} administrators={len(administrators)}")
        return 0
    if command == "stage":
        version = service.stage(
            organization_id=args.organization,
            credential_env=args.credential_env,
            policy_path=args.file,
        )
        _print_policy_version("policy staged", version)
        return 0
    if command == "activate":
        activation = service.activate(
            organization_id=args.organization,
            credential_env=args.credential_env,
            version_id=args.version,
        )
        _print_policy_activation("policy activated", activation)
        return 0
    if command == "rollback":
        activation = service.rollback(
            organization_id=args.organization,
            credential_env=args.credential_env,
            version_id=args.version,
        )
        _print_policy_activation("policy rolled back", activation)
        return 0
    if command == "status":
        status = service.status(
            organization_id=args.organization,
            credential_env=args.credential_env,
        )
        _print_policy_status(status, as_json=args.json)
        return 0
    if command == "administrator":
        if args.policy_administrator_command == "grant":
            administrator = service.grant_oidc_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                issuer=args.issuer,
                subject=args.subject,
            )
            print(
                "policy administrator granted: "
                f"organization={administrator.organization_id} issuer={administrator.issuer} subject={administrator.subject}"
            )
            return 0
        if args.policy_administrator_command == "revoke":
            service.revoke_oidc_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                issuer=args.issuer,
                subject=args.subject,
            )
            print(f"policy administrator revoked: organization={args.organization} issuer={args.issuer} subject={args.subject}")
            return 0
        if args.policy_administrator_command == "revoke-static":
            service.revoke_static_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                actor_id=args.actor_id,
            )
            print(f"static policy administrator revoked: organization={args.organization} actor_id={args.actor_id}")
            return 0
    if command == "break-glass" and args.policy_break_glass_command == "recover":
        try:
            recovery_secret = getpass.getpass("Hormuz break-glass recovery secret: ")
        except (EOFError, OSError):
            raise PolicyControlError("policy_break_glass_credential_unavailable") from None
        administrator = service.break_glass_recover(
            organization_id=args.organization,
            recovery_secret=recovery_secret,
            issuer=args.issuer,
            subject=args.subject,
            reason_code=args.reason_code,
        )
        print(
            "policy break-glass recovery completed: "
            f"organization={administrator.organization_id} issuer={administrator.issuer} subject={administrator.subject}"
        )
        return 0
    raise ConfigError("unsupported policy control command")


def _print_policy_version(prefix: str, version: PolicyVersionRecord) -> None:
    print(
        f"{prefix}: organization={version.organization_id} version={version.version_id} "
        f"sha256={version.content_sha256} created_at={version.created_at.isoformat()}"
    )


def _print_policy_activation(prefix: str, activation: PolicyActivation) -> None:
    print(
        f"{prefix}: organization={activation.organization_id} version={activation.version_id} "
        f"generation={activation.generation} activated_at={activation.activated_at.isoformat()}"
    )


def _print_policy_status(status: PolicyControlStatus, *, as_json: bool) -> None:
    payload = {
        "organization_id": status.organization_id,
        "initialized": status.initialized,
        "active": (
            {
                "version_id": status.active.version_id,
                "generation": status.active.generation,
                "activated_at": status.active.activated_at.isoformat(),
                "activated_by_kind": status.active.activated_by_kind,
                "activated_by_identity_key": status.active.activated_by_identity_key,
            }
            if status.active is not None
            else None
        ),
        "versions": [
            {
                "version_id": version.version_id,
                "content_sha256": version.content_sha256,
                "created_at": version.created_at.isoformat(),
                "author_kind": version.author_kind,
                "author_identity_key": version.author_identity_key,
                "change_summary": version.change_summary,
            }
            for version in status.versions
        ],
        "administrators": [administrator.audit_ref() for administrator in status.administrators],
    }
    if as_json:
        print(json.dumps(contract_envelope(POLICY_CONTROL_STATUS_SCHEMA_ID, payload), indent=2, sort_keys=True))
        return
    active = payload["active"]
    print(f"organization: {status.organization_id}")
    print(f"initialized: {str(status.initialized).lower()}")
    print(f"active policy: {active['version_id'] if isinstance(active, dict) else '-'}")
    print(f"active generation: {active['generation'] if isinstance(active, dict) else '-'}")
    print(f"policy versions: {len(status.versions)}")
    print(f"active policy administrators: {len(status.administrators)}")


def _client_config(
    config: GatewayConfig,
    client: str,
    url: str | None,
    *,
    actor_id: str | None = None,
    auth_mode: str = "auto",
    credential_env: str | None = None,
) -> int:
    base_url = _client_base_url(url or f"http://{config.listen.host}:{config.listen.port}")
    if actor_id is None:
        identity = next(iter(config.identities_by_actor.values()))
    else:
        identity = config.identities_by_actor.get(actor_id)
        if identity is None:
            raise ConfigError(f"Unknown actor: {actor_id}")
    static_identity = next(
        (item for item in config.identities_by_token.values() if item.actor_id == identity.actor_id),
        None,
    )
    oidc_identity = next(
        (item for item in config.identities_by_subject.values() if item.actor_id == identity.actor_id),
        None,
    )
    if auth_mode == "static":
        if static_identity is None:
            raise ConfigError(f"Actor {identity.actor_id} has no static identity")
        selected_identity = static_identity
        uses_oidc = False
    elif auth_mode == "oidc":
        if oidc_identity is None:
            raise ConfigError(f"Actor {identity.actor_id} has no OIDC subject mapping")
        selected_identity = oidc_identity
        uses_oidc = True
    elif static_identity is not None:
        selected_identity = static_identity
        uses_oidc = False
    elif oidc_identity is not None:
        selected_identity = oidc_identity
        uses_oidc = True
    else:  # pragma: no cover - configuration requires at least one source
        raise ConfigError(f"Actor {identity.actor_id} has no authentication source")
    env_name = credential_env or (
        "HORMUZ_OIDC_ACCESS_TOKEN" if uses_oidc else selected_identity.token_env
    )
    if not env_name or not env_name.replace("_", "A").isalnum() or env_name[0].isdigit():
        raise ConfigError("credential environment variable must contain only letters, digits, and underscores")
    if client == "codex":
        policy = PolicyRuntime(config).snapshot_for(selected_identity).effective_policy
        allowed_models = set(policy.allowed_models) if policy.allowed_models is not None else None
        default_model = next(
            (
                alias
                for alias, route in config.model_routes.items()
                if route.protocol == "openai" and (allowed_models is None or alias in allowed_models)
            ),
            None,
        )
        if default_model is None:
            raise ConfigError(f"Identity {selected_identity.actor_id} has no allowed OpenAI model for Codex")
        lines = [
            "# Put this in the user-level ~/.codex/config.toml",
            f"model = {json.dumps(default_model)}",
            'model_provider = "hormuz"',
            "",
            "[model_providers.hormuz]",
            'name = "Hormuz"',
            f"base_url = {json.dumps(base_url + '/v1')}",
            'wire_api = "responses"',
        ]
        if uses_oidc:
            lines.extend(
                [
                    "",
                    "[model_providers.hormuz.auth]",
                    'command = "hormuz"',
                    f'args = ["auth", "token", "--env", "{env_name}"]',
                    "refresh_interval_ms = 300000",
                ]
            )
        else:
            lines.insert(-1, f'env_key = "{env_name}"')
        print("\n".join(lines))
    else:
        if uses_oidc:
            print("# Put this JSON in the managed or user Claude Code settings file:")
            print(
                json.dumps(
                    {
                        "apiKeyHelper": f"hormuz auth token --env {env_name}",
                        "env": {
                            "ANTHROPIC_BASE_URL": base_url,
                            "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "300000",
                        },
                    },
                    indent=2,
                )
            )
            print(f'# Ensure {env_name} contains a current OIDC JWT access token.')
        else:
            print(f"export ANTHROPIC_BASE_URL={shlex.quote(base_url)}")
            print(f'export ANTHROPIC_AUTH_TOKEN="${{{env_name}}}"')
        print("claude")
    return 0


def _auth_token(env_name: str) -> int:
    value = os.environ.get(env_name, "")
    if not value:
        print(f"credential environment variable is not set: {env_name}", file=sys.stderr)
        return 1
    if len(value.encode("utf-8")) > 64 * 1024 or "\n" in value or "\r" in value:
        print(f"credential environment variable is invalid: {env_name}", file=sys.stderr)
        return 1
    print(value)
    return 0


def _client_base_url(value: str) -> str:
    result = value.rstrip("/")
    parsed = urlparse(result)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(character in result for character in ("\n", "\r", "\x00"))
    ):
        raise ConfigError("client gateway URL must be an HTTP(S) URL without credentials, query, or fragment")
    return result


def _audit_export(config: GatewayConfig, args: argparse.Namespace) -> int:
    try:
        since = _audit_since(args.since)
    except ValueError as error:
        print(f"invalid --since: {error}", file=sys.stderr)
        return 2
    events = create_usage_store(config).audit_events(
        since=since,
        kind=args.kind,
        organization_id=_required_organization(config),
    )
    stream = sys.stdout
    should_close = False
    output_path: Path | None = None
    if args.output != "-":
        output_path = Path(args.output).expanduser().absolute()
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | (os.O_TRUNC if args.force else os.O_EXCL)
        )
        try:
            descriptor = os.open(output_path, flags, 0o600)
        except FileExistsError:
            print(f"audit export already exists: {output_path} (use --force to replace it)", file=sys.stderr)
            return 2
        except OSError as error:
            print(f"cannot open audit export {output_path}: {error}", file=sys.stderr)
            return 2
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows permission semantics
            os.chmod(output_path, 0o600)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        should_close = True

    digest = hashlib.sha256()
    try:
        for event in events:
            line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            stream.write(line)
            digest.update(line.encode("utf-8"))
        stream.flush()
        if should_close:
            os.fsync(stream.fileno())
    finally:
        if should_close:
            stream.close()
    destination = str(output_path) if output_path is not None else "stdout"
    print(
        f"exported {len(events)} events to {destination}; sha256={digest.hexdigest()}",
        file=sys.stderr,
    )
    return 0


def _audit_anchor(config: GatewayConfig, args: argparse.Namespace) -> int:
    """Create and externally retain one verified, metadata-only audit snapshot."""

    try:
        since = _audit_since(args.since)
    except ValueError as error:
        print(f"invalid --since: {error}", file=sys.stderr)
        return 2
    organization_id = _required_organization(config)
    events = create_usage_store(config).audit_events(
        since=since,
        kind=args.kind,
        organization_id=organization_id,
    )
    artifact = build_audit_anchor_artifact(events, organization_id=organization_id)
    artifact_id, head_digest, event_count = audit_anchor_summary(artifact)
    serialized = serialize_audit_anchor_artifact(artifact)
    anchor_config = config.audit_anchor
    if anchor_config is None:
        raise CustodyError("audit_anchor_unconfigured")
    retention_until = datetime.now(timezone.utc) + timedelta(days=anchor_config.retention_days)
    receipt = create_audit_anchor_sink(config).anchor(
        serialized,
        artifact_id=artifact_id,
        organization_id=organization_id,
        head_digest=head_digest,
        retention_until=retention_until,
        legal_hold=anchor_config.legal_hold,
    )
    version = f" object_version={receipt.object_version}" if receipt.object_version else ""
    print(
        f"audit_anchor={receipt.backend} artifact_id={receipt.artifact_id} events={event_count} "
        f"artifact_sha256={receipt.artifact_sha256} "
        f"head_digest={receipt.head_digest}{version}",
        file=sys.stderr,
    )
    return 0


def _audit_chain(config: GatewayConfig, args: argparse.Namespace) -> int:
    """Operate commit-time evidence without placing Object Lock on request egress."""

    organization_id = _required_organization(config)
    store = create_usage_store(config)
    if args.audit_chain_command == "status":
        head = store.audit_chain_head(organization_id=organization_id)
        maximum_age = (
            config.audit_chain.maximum_anchor_age_seconds
            if config.audit_chain is not None
            else None
        )
        status = store.audit_chain_anchor_status(
            organization_id=organization_id,
            maximum_age_seconds=maximum_age,
        )
        checkpoint_at = status.latest_checkpoint_at.isoformat() if status.latest_checkpoint_at is not None else "none"
        oldest_unanchored = (
            status.oldest_unanchored_at.isoformat() if status.oldest_unanchored_at is not None else "none"
        )
        digest = head.head_digest or "none"
        print(
            f"audit_chain=ready organization={organization_id} chain_version={head.chain_version} "
            f"chain_epoch={head.chain_epoch} sequence={head.sequence} head_digest={digest} "
            f"latest_checkpoint_at={checkpoint_at} oldest_unanchored_at={oldest_unanchored} "
            f"anchor_overdue={str(status.overdue).lower()}"
        )
        return 0
    if args.audit_chain_command == "anchor":
        anchor_config = config.audit_anchor
        if anchor_config is None:
            raise CustodyError("audit_anchor_unconfigured")
        head = store.audit_chain_head(organization_id=organization_id)
        checkpoint = build_audit_chain_checkpoint(head)
        serialized = serialize_audit_chain_checkpoint(checkpoint)
        # Preserve the exact canonical input required by a later recovery
        # operation before egress.  It contains metadata only, but is still
        # owner-only by default to avoid casually exposing tenant topology.
        _write_audit_chain_checkpoint(Path(args.output).expanduser().absolute(), serialized, force=args.force)
        checkpoint_id, checkpoint_organization, _, sequence, head_digest = audit_chain_checkpoint_summary(checkpoint)
        if checkpoint_organization != organization_id:
            raise CustodyError("audit_chain_tenant_mismatch")
        retention_until = datetime.now(timezone.utc) + timedelta(days=anchor_config.retention_days)
        receipt = create_audit_anchor_sink(config).anchor(
            serialized,
            artifact_id=checkpoint_id,
            organization_id=organization_id,
            head_digest=head_digest,
            retention_until=retention_until,
            legal_hold=anchor_config.legal_hold,
        )
        if (
            receipt.artifact_id != checkpoint_id
            or receipt.head_digest != head_digest
            or not _is_sha256_digest(receipt.artifact_sha256)
        ):
            raise CustodyError("audit_chain_anchor_receipt_invalid")
        store.record_audit_chain_checkpoint(
            checkpoint=checkpoint,
            artifact_sha256=receipt.artifact_sha256,
            anchor_backend=receipt.backend,
            object_version=receipt.object_version,
        )
        version = f" object_version={receipt.object_version}" if receipt.object_version else ""
        print(
            f"audit_chain_anchor={receipt.backend} checkpoint_id={checkpoint_id} "
            f"chain_epoch={head.chain_epoch} sequence={sequence} artifact_sha256={receipt.artifact_sha256} "
            f"head_digest={head_digest}{version}"
        )
        return 0
    checkpoint = _read_audit_chain_checkpoint(Path(args.checkpoint).expanduser().absolute())
    if args.audit_chain_command == "verify":
        head = store.verify_audit_chain(organization_id=organization_id, checkpoint=checkpoint)
        checkpoint_id, _, _, sequence, head_digest = audit_chain_checkpoint_summary(checkpoint)
        print(
            f"audit_chain_verified=true organization={organization_id} checkpoint_id={checkpoint_id} "
            f"checkpoint_sequence={sequence} checkpoint_head_digest={head_digest} "
            f"chain_epoch={head.chain_epoch} sequence={head.sequence}"
        )
        return 0
    if args.audit_chain_command == "epoch":
        if args.confirm != "START_NEW_AUDIT_CHAIN_EPOCH":
            raise CustodyError("audit_chain_epoch_confirmation_required")
        _, checkpoint_organization, _, _, _ = audit_chain_checkpoint_summary(checkpoint)
        if checkpoint_organization != organization_id:
            raise CustodyError("audit_chain_tenant_mismatch")
        head = store.begin_audit_chain_epoch(checkpoint=checkpoint, reason_code=args.reason)
        print(
            f"audit_chain_epoch_started=true organization={organization_id} reason={args.reason} "
            f"chain_epoch={head.chain_epoch} sequence={head.sequence} head_digest={head.head_digest}"
        )
        return 0
    raise CustodyError("audit_chain_command_unsupported")


def _write_audit_chain_checkpoint(path: Path, serialized: bytes, *, force: bool) -> None:
    """Publish one canonical checkpoint without exposing a partial target file."""

    descriptor: int | None = None
    temporary_path: str | None = None
    try:
        # Stage in the target directory so the final hard-link or replacement
        # is atomic. In particular, --force must preserve the prior recovery
        # artifact if staging fails halfway through a disk write.
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(serialized)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("checkpoint write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if force:
            os.replace(temporary_path, path)
        else:
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                raise CustodyError("audit_chain_checkpoint_exists") from None
        try:
            os.unlink(temporary_path)
        except OSError:
            # The published checkpoint is valid; a private staging remnant is
            # safe to clean up later rather than converting success to failure.
            pass
        temporary_path = None
    except CustodyError:
        raise
    except OSError:
        raise CustodyError("audit_chain_checkpoint_write_unavailable") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _read_audit_chain_checkpoint(path: Path) -> dict[str, object]:
    try:
        artifact = path.read_bytes()
    except OSError:
        raise CustodyError("audit_chain_checkpoint_unavailable") from None
    try:
        return parse_audit_chain_checkpoint(artifact)
    except AuditChainError:
        raise


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _custody(config: GatewayConfig, args: argparse.Namespace) -> int:
    if args.custody_command in {"bootstrap", "status", "administrator", "authorize", "approve"}:
        return _custody_control(config, args)
    if config.custody_control.mode == "postgresql":
        raise CustodyControlError("custody_governed_executor_required")
    if args.custody_command == "verify":
        return _custody_verify(config)
    if args.custody_command == "seal":
        return _custody_seal(config, args)
    if args.custody_command == "rewrap":
        return _custody_rewrap(config, args)
    raise CustodyError("custody_command_unsupported")


def _custody_control(config: GatewayConfig, args: argparse.Namespace) -> int:
    """Run human authorization through the custody-control service only."""

    service = CustodyControlService(config)
    command = args.custody_command
    if command == "bootstrap":
        administrators = service.bootstrap(
            organization_id=args.organization,
            credential_env=args.credential_env,
        )
        print(f"custody bootstrap initialized: organization={args.organization} administrators={len(administrators)}")
        return 0
    if command == "status":
        _print_custody_status(
            service.status(
                organization_id=args.organization,
                credential_env=args.credential_env,
            ),
            as_json=args.json,
        )
        return 0
    if command == "administrator":
        if args.custody_administrator_command == "grant":
            administrator = service.grant_oidc_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                issuer=args.issuer,
                subject=args.subject,
            )
            print(
                "custody administrator granted: "
                f"organization={administrator.organization_id} issuer={administrator.issuer} "
                f"subject={administrator.subject}"
            )
            return 0
        if args.custody_administrator_command == "revoke":
            service.revoke_oidc_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                issuer=args.issuer,
                subject=args.subject,
            )
            print(
                "custody administrator revoked: "
                f"organization={args.organization} issuer={args.issuer} subject={args.subject}"
            )
            return 0
        if args.custody_administrator_command == "revoke-static":
            service.revoke_static_administrator(
                organization_id=args.organization,
                credential_env=args.credential_env,
                actor_id=args.actor_id,
            )
            print(
                "static custody administrator revoked: "
                f"organization={args.organization} actor_id={args.actor_id}"
            )
            return 0
    if command == "authorize":
        operation = service.authorize_operation(
            organization_id=args.organization,
            credential_env=args.credential_env,
            operation_type=args.operation,
            target_sha256=args.target_sha256,
            parameters_sha256=args.parameters_sha256,
            protected_input_ref_sha256=args.protected_input_ref_sha256,
        )
        _print_custody_operation("custody operation recorded", operation)
        return 0
    if command == "approve":
        operation = service.approve_operation(
            organization_id=args.organization,
            credential_env=args.credential_env,
            operation_id=args.operation_id,
        )
        _print_custody_operation("custody operation approved", operation)
        return 0
    raise CustodyControlError("custody_control_command_unsupported")


def _print_custody_operation(prefix: str, operation: CustodyOperationIntent) -> None:
    print(
        f"{prefix}: organization={operation.organization_id} operation_id={operation.operation_id} "
        f"operation={operation.operation_type} state={operation.effective_state()} "
        f"approvals={len(operation.approvals)}/{operation.required_approvals}"
    )


def _print_custody_status(status: CustodyControlStatus, *, as_json: bool) -> None:
    payload = {
        "organization_id": status.organization_id,
        "initialized": status.initialized,
        "administrators": [administrator.audit_ref() for administrator in status.administrators],
        "operation_count": status.operation_count,
        "operations": [
            {
                "operation_id": operation.operation_id,
                "operation_type": operation.operation_type,
                "risk_level": operation.risk_level,
                "target_kind": operation.target_kind,
                "target_sha256": operation.target_sha256,
                "parameters_sha256": operation.parameters_sha256,
                "protected_input_ref_sha256": operation.protected_input_ref_sha256,
                "state": operation.effective_state(),
                "required_approvals": operation.required_approvals,
                "approval_count": len(operation.approvals),
                "created_at": operation.created_at.isoformat(),
                "expires_at": operation.expires_at.isoformat(),
                "authorized_at": operation.authorized_at.isoformat() if operation.authorized_at else None,
                "requested_by_kind": operation.requested_by_kind,
                "requested_by_identity_key": operation.requested_by_identity_key,
                "approvals": [
                    {
                        "approver_kind": approval.approver_kind,
                        "approver_identity_key": approval.approver_identity_key,
                        "approved_at": approval.approved_at.isoformat(),
                    }
                    for approval in operation.approvals
                ],
            }
            for operation in status.operations
        ],
    }
    if as_json:
        print(json.dumps(contract_envelope(CUSTODY_CONTROL_STATUS_SCHEMA_ID, payload), indent=2, sort_keys=True))
        return
    print(f"organization: {status.organization_id}")
    print(f"initialized: {str(status.initialized).lower()}")
    print(f"active custody administrators: {len(status.administrators)}")
    print(f"custody operations: {status.operation_count}")
    print(f"operations shown: {len(status.operations)}")


def _custody_verify(config: GatewayConfig) -> int:
    """Exercise the configured custody profile without writing an audit object."""

    from .aws_custody import AWSKMSKeyCustodian, S3ObjectLockAuditAnchorSink, verify_aws_kms_profile
    from .openbao_custody import OpenBaoTransitDataKeyProvider, verify_openbao_transit_profile
    from .self_hosted_custody import EncryptedS3ObjectLockAuditAnchorSink

    key_custody = config.key_custody
    anchor_config = config.audit_anchor
    if key_custody is None or anchor_config is None:
        raise CustodyError("custody_profile_unconfigured")
    provider = create_data_key_provider(config)
    sink = create_audit_anchor_sink(config)
    organization_id = _required_organization(config)
    if key_custody.backend == "aws-kms":
        if not isinstance(provider, AWSKMSKeyCustodian) or not isinstance(sink, S3ObjectLockAuditAnchorSink):
            raise CustodyError("custody_profile_backend_unsupported")
        statuses = verify_aws_kms_profile(
            provider,
            key_custody.key_references,
            organization_id=organization_id,
        )
        sink.verify_configuration()
        print(
            f"key_custody=aws-kms verified_purposes={len(statuses)} data_key_round_trip=passed "
            "audit_anchor=aws-s3-object-lock object_lock=enabled versioning=enabled",
        )
        return 0
    if key_custody.backend == "openbao-transit":
        if not isinstance(provider, OpenBaoTransitDataKeyProvider) or not isinstance(
            sink, EncryptedS3ObjectLockAuditAnchorSink
        ):
            raise CustodyError("custody_profile_backend_unsupported")
        verified = verify_openbao_transit_profile(
            provider,
            key_custody.key_references,
            organization_id=organization_id,
        )
        sink.verify_configuration()
        print(
            f"key_custody=openbao-transit verified_purposes={verified} data_key_round_trip=passed "
            "audit_anchor=s3-compatible-object-lock payload_encryption=envelope "
            "object_lock=enabled versioning=enabled",
        )
        return 0
    raise CustodyError("custody_profile_backend_unsupported")


def _custody_seal(config: GatewayConfig, args: argparse.Namespace) -> int:
    source = os.environ.get(args.input_env, "")
    if not source:
        raise CustodyError("custody_input_unavailable")
    if "\x00" in source or "\r" in source or "\n" in source:
        raise CustodyError("custody_input_invalid")
    key_custody = config.key_custody
    if key_custody is None:
        raise CustodyError("key_custody_unconfigured")
    organization_id = _required_organization(config)
    envelope = EnvelopeCipher(create_data_key_provider(config)).seal(
        source.encode("utf-8"),
        organization_id=organization_id,
        purpose=args.purpose,
        key_reference=key_custody.key_reference_for(args.purpose),
    )
    write_envelope_file(Path(args.output).expanduser().absolute(), envelope, force=args.force)
    print(
        f"sealed_envelope={envelope.purpose} sha256={hashlib.sha256(serialize_envelope(envelope)).hexdigest()}",
    )
    return 0


def _custody_rewrap(config: GatewayConfig, args: argparse.Namespace) -> int:
    key_custody = config.key_custody
    if key_custody is None:
        raise CustodyError("key_custody_unconfigured")
    envelope = read_envelope_file(Path(args.input).expanduser().absolute())
    if envelope.organization_id != _required_organization(config):
        raise CustodyError("encrypted_envelope_organization_invalid")
    rewrapped = EnvelopeCipher(create_data_key_provider(config)).rewrap(
        envelope,
        destination_key_reference=key_custody.key_reference_for(envelope.purpose),
    )
    write_envelope_file(Path(args.output).expanduser().absolute(), rewrapped, force=args.force)
    print(
        f"rewrapped_envelope={rewrapped.purpose} sha256={hashlib.sha256(serialize_envelope(rewrapped)).hexdigest()}",
    )
    return 0


def _audit_since(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _required_organization(config: GatewayConfig) -> str:
    organization_ids = config.organization_ids
    if len(organization_ids) != 1:
        raise ConfigError(
            "usage reporting, audit export, and encrypted custody commands require exactly one configured organization; "
            "use a tenant-scoped configuration"
        )
    return organization_ids[0]


def _storage(config: GatewayConfig, args: argparse.Namespace) -> int:
    if args.storage_command == "verify":
        runtime_pool = create_postgres_runtime_pool(config)
        try:
            create_usage_store(config, connection_pool=runtime_pool)
            print(f"usage storage verified: {config.usage_storage.backend}")
        finally:
            _close_runtime_pool(runtime_pool)
        return 0
    if args.storage_command == "migrate":
        if config.usage_storage.backend == "sqlite":
            create_usage_store(config)
            print("SQLite usage storage migration is current")
            return 0
        status = migrate_postgres(
            postgres_migration_dsn(config),
            schema=config.usage_storage.postgres_schema,
            runtime_role=config.usage_storage.postgres_runtime_role,
            policy_control_role=config.policy_control.postgres_control_role,
            custody_control_role=config.custody_control.postgres_control_role,
        )
        print(f"PostgreSQL usage storage migration is current: v{status.version}")
        return 0
    raise ConfigError("unsupported storage command")


def _close_runtime_pool(pool: PostgresConnectionPool | None) -> None:
    """Close a one-shot diagnostic pool after its final verification query."""

    if pool is not None:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
