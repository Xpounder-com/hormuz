from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shlex
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .auth import AuthenticationError, Authenticator
from .config import ConfigError, GatewayConfig, Identity
from .context import (
    CLASSIFICATIONS,
    ContextError,
    ContextLifecycleSnapshot,
    ContextPackRequest,
    ContextPrincipal,
    ContextRecord,
    build_context_pack,
)
from .context_store import (
    ContextStoreError,
    SQLiteContextRepository,
    StoredContextRecord,
)
from .dlp_client import DLPApprovalClient, DLPApprovalClientError
from .credential_store import CredentialStoreError, validate_profile
from .context_benchmark import (
    ContextBenchmarkError,
    DEFAULT_CORPUS_PATH,
    DEFAULT_REFERENCES_PATH,
    run_benchmark,
    write_benchmark_result,
)
from .mcp import (
    MCPConfigurationError,
    run_mcp_server,
    validate_credential_env,
    validate_gateway_url,
)
from .policy import PolicyEngine
from .server import GatewayServer
from .session_client import (
    SessionClientError,
    access_token as session_access_token,
    login as session_login,
    logout as session_logout,
)
from .session_admin_client import SessionAdminClient, SessionAdminClientError
from .store import UsageStore


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

    connect = subparsers.add_parser("client-config", help="Print client configuration for this gateway")
    connect.add_argument("client", choices=["codex", "claude"])
    connect.add_argument("--url", help="Externally reachable gateway URL; defaults to configured listener")
    connect.add_argument("--actor", help="Configured actor ID; defaults to the first configured actor")
    connect.add_argument(
        "--auth-mode",
        choices=["auto", "static", "oidc", "session"],
        default="auto",
        help="Credential source to configure (default: static when available, otherwise OIDC)",
    )
    connect.add_argument(
        "--credential-env",
        help="Environment variable containing the credential (OIDC default: HORMUZ_OIDC_ACCESS_TOKEN)",
    )
    connect.add_argument(
        "--profile",
        help="Secure-store profile for session auth (default: codex or claude)",
    )

    login_parser = subparsers.add_parser(
        "login",
        help="Sign in through company OIDC and save a revocable session in the OS secure store",
    )
    login_parser.add_argument("--gateway", required=True, help="Hormuz gateway base URL")
    login_parser.add_argument("--profile", default="default", help="Local secure-store profile")
    login_parser.add_argument(
        "--client",
        choices=["codex", "claude-code"],
        default="codex",
        help="AI client bound to this session (default: codex)",
    )
    login_parser.add_argument("--issuer", help="OIDC issuer URL; required when multiple login issuers exist")
    login_parser.add_argument("--no-open", action="store_true", help="Print the login URL instead of opening a browser")
    login_parser.add_argument(
        "--wait-seconds",
        type=int,
        default=300,
        help="Time to wait for browser login, from 30 to 600 seconds (default: 300)",
    )
    login_parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="Allow loopback HTTP for local development only",
    )

    logout_parser = subparsers.add_parser("logout", help="Revoke and remove a saved Hormuz session")
    logout_parser.add_argument("--gateway", required=True, help="Hormuz gateway base URL")
    logout_parser.add_argument("--profile", default="default", help="Local secure-store profile")
    logout_parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="Allow loopback HTTP for local development only",
    )

    mcp = subparsers.add_parser(
        "mcp",
        help="Run the governed-context MCP stdio adapter for Codex and Claude Code",
    )
    mcp.add_argument("--url", required=True, help="Hormuz gateway base URL")
    mcp.add_argument(
        "--credential-env",
        default="HORMUZ_TOKEN",
        help="Environment variable containing the Hormuz credential (default: HORMUZ_TOKEN)",
    )
    mcp.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="Context API timeout from 1 to 60 seconds (default: 30)",
    )

    mcp_config = subparsers.add_parser(
        "mcp-config",
        help="Print a secret-free Codex or Claude Code MCP configuration",
    )
    mcp_config.add_argument("client", choices=["codex", "claude"])
    mcp_config.add_argument("--url", required=True, help="Hormuz gateway base URL")
    mcp_config.add_argument(
        "--credential-env",
        default="HORMUZ_TOKEN",
        help="Environment variable inherited by the MCP process (default: HORMUZ_TOKEN)",
    )
    mcp_config.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="Context API timeout from 1 to 60 seconds (default: 30)",
    )

    auth = subparsers.add_parser("auth", help="Credential helpers for AI clients")
    auth_subparsers = auth.add_subparsers(dest="auth_command", required=True)
    auth_token = auth_subparsers.add_parser("token", help="Print a current AI-client credential")
    auth_token.add_argument("--env", help="Legacy workload credential environment variable")
    auth_token.add_argument("--gateway", help="Hormuz gateway for a saved human session")
    auth_token.add_argument(
        "--gateway-env",
        help="Environment variable containing the Hormuz gateway URL",
    )
    auth_token.add_argument("--profile", default="default", help="Local secure-store profile")
    auth_token.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="Allow loopback HTTP for local development only",
    )

    dlp = subparsers.add_parser("dlp", help="Review organization DLP exceptions")
    dlp_subparsers = dlp.add_subparsers(dest="dlp_command", required=True)
    approval = dlp_subparsers.add_parser("approval", help="Inspect or approve one DLP exception")
    approval_subparsers = approval.add_subparsers(
        dest="dlp_approval_command",
        required=True,
    )
    for action in ("show", "approve"):
        command = approval_subparsers.add_parser(
            action,
            help=(
                "Show metadata for one DLP approval request"
                if action == "show"
                else "Approve one exact DLP request for a single retry"
            ),
        )
        command.add_argument("request_id", help="Opaque apr_ approval request ID")
        command.add_argument("--gateway", required=True, help="Hormuz gateway base URL")
        credential = command.add_mutually_exclusive_group()
        credential.add_argument(
            "--credential-env",
            help="Approver credential environment variable (default: HORMUZ_TOKEN)",
        )
        credential.add_argument(
            "--profile",
            help="Saved human-session profile instead of an environment credential",
        )
        command.add_argument(
            "--allow-insecure-http",
            action="store_true",
            help="Allow loopback HTTP for local development only",
        )

    sessions = subparsers.add_parser(
        "sessions",
        help="Inspect and revoke human sessions as a tenant administrator",
    )
    session_subparsers = sessions.add_subparsers(dest="sessions_command", required=True)
    session_list = session_subparsers.add_parser(
        "list",
        help="List active human sessions in the administrator's organization",
    )
    session_list.add_argument("--actor", help="Filter by exact actor ID")
    session_list.add_argument("--team", help="Filter by exact team ID")
    session_list.add_argument("--limit", type=int, default=50, help="Page size from 1 to 100")
    session_list.add_argument("--cursor", help="Opaque cursor returned by the previous page")
    session_revoke = session_subparsers.add_parser(
        "revoke",
        help="Immediately revoke a session, actor, team, or organization",
    )
    session_revoke.add_argument(
        "--scope",
        required=True,
        choices=["session", "actor", "team", "organization"],
    )
    session_revoke.add_argument(
        "--target",
        help="Session, actor, or team ID; omit only for organization scope",
    )
    session_revoke.add_argument(
        "--reason",
        required=True,
        choices=[
            "access_change",
            "employment_change",
            "security_incident",
            "administrative",
        ],
    )
    session_events = session_subparsers.add_parser(
        "events",
        help="List metadata-only session security events in the administrator's organization",
    )
    session_events.add_argument("--actor", help="Filter by exact target actor ID")
    session_events.add_argument("--team", help="Filter by exact target team ID")
    session_events.add_argument(
        "--event-type",
        choices=[
            "refresh_replay",
            "logout",
            "authorization_mapping_removed",
            "admin_revocation",
        ],
        help="Filter by security event type",
    )
    session_events.add_argument("--since", help="UTC ISO-8601 lower bound")
    session_events.add_argument("--limit", type=int, default=50, help="Page size from 1 to 100")
    session_events.add_argument("--cursor", help="Opaque cursor returned by the previous page")
    for command in (session_list, session_revoke, session_events):
        command.add_argument("--gateway", required=True, help="Hormuz gateway base URL")
        credential = command.add_mutually_exclusive_group()
        credential.add_argument(
            "--credential-env",
            help="Administrator credential environment variable (default: HORMUZ_TOKEN)",
        )
        credential.add_argument(
            "--profile",
            help="Saved human-session profile instead of an environment credential",
        )
        command.add_argument(
            "--allow-insecure-http",
            action="store_true",
            help="Allow loopback HTTP for local development only",
        )

    audit = subparsers.add_parser("audit-export", help="Export metadata-only usage and security events as JSONL")
    audit.add_argument("--kind", choices=["all", "usage", "security"], default="all")
    audit.add_argument("--since", help="UTC ISO-8601 lower bound (default: start of current month)")
    audit.add_argument("--output", default="-", help="Output path or - for stdout (default: -)")
    audit.add_argument("--force", action="store_true", help="Allow replacing an existing output file")

    context = subparsers.add_parser(
        "context-pack",
        help="Build an explicit governed context pack from the repository or JSONL",
    )
    context.add_argument(
        "--records",
        help="Compatibility path to content-bearing context JSONL; defaults to the repository",
    )
    context.add_argument(
        "--snapshot-file",
        help="Trusted lifecycle snapshot envelope for --records; repository mode loads the stored snapshot",
    )
    context.add_argument("--query", required=True, help="Task or question used for lexical retrieval")
    context.add_argument("--organization", required=True, help="Organization scope ID")
    context.add_argument("--actor", required=True, help="Configured actor ID")
    context.add_argument("--repository", help="Repository scope ID; omitted means organization-only context")
    context.add_argument("--branch", help="Branch scope; requires --repository")
    context.add_argument(
        "--clearance",
        choices=CLASSIFICATIONS,
        default="internal",
        help="Maximum permitted classification (default: internal)",
    )
    context.add_argument("--token-budget", type=int, required=True, help="Maximum estimated context tokens")
    context.add_argument("--max-items", type=int, default=20, help="Maximum records in the pack (default: 20)")
    context.add_argument(
        "--policy-version",
        default="local-v1",
        help="Policy version included in the deterministic pack identity",
    )
    context.add_argument("--as-of", help="UTC ISO-8601 evaluation time (default: now)")
    context.add_argument(
        "--include-provisional",
        action="store_true",
        help="Include provisional records; verified-only is the default",
    )

    context_import = subparsers.add_parser(
        "context-import",
        help="Idempotently import governed context records from JSONL",
    )
    context_import.add_argument("--records", required=True, help="Path to content-bearing context JSONL")
    context_import.add_argument("--actor", required=True, help="Configured actor ID for scope and audit")
    context_import.add_argument(
        "--policy-version",
        default="local-v1",
        help="Policy version recorded in metadata-only mutation audit events",
    )

    snapshot_import = subparsers.add_parser(
        "context-snapshot-import",
        help="Record an idempotent repository/dependency lifecycle snapshot",
    )
    snapshot_import.add_argument("--snapshot", required=True, help="Lifecycle snapshot envelope JSON")
    snapshot_import.add_argument("--actor", required=True, help="Configured actor ID for scope and audit")
    snapshot_import.add_argument(
        "--expected-version",
        type=int,
        help="Required current version when replacing a different stored snapshot",
    )
    snapshot_import.add_argument(
        "--policy-version",
        default="local-v1",
        help="Policy version recorded in metadata-only lifecycle audit events",
    )

    snapshot_show = subparsers.add_parser(
        "context-snapshot-show",
        help="Show the trusted lifecycle snapshot for a repository branch",
    )
    snapshot_show.add_argument("--actor", required=True, help="Configured actor defining organization scope")
    snapshot_show.add_argument("--repository", required=True, help="Repository scope ID")
    snapshot_show.add_argument("--branch", required=True, help="Branch scope")

    context_list = subparsers.add_parser(
        "context-list",
        help="List governed context authorized for a configured actor",
    )
    _add_context_read_arguments(context_list)
    context_list.add_argument(
        "--include-content",
        action="store_true",
        help="Include content; metadata-only is the default",
    )

    context_export = subparsers.add_parser(
        "context-export",
        help="Export authorized content-bearing context as private JSONL",
    )
    _add_context_read_arguments(context_export)
    context_export.add_argument("--output", required=True, help="Output path or - for explicit stdout")
    context_export.add_argument("--force", action="store_true", help="Allow replacing an existing output file")

    context_delete = subparsers.add_parser(
        "context-delete",
        help="Delete one governed context record using optimistic concurrency",
    )
    context_delete.add_argument("--actor", required=True, help="Configured actor ID for scope and audit")
    context_delete.add_argument("--record-id", required=True, help="Context record ID")
    context_delete.add_argument("--expected-version", required=True, type=int, help="Current storage version")
    context_delete.add_argument(
        "--policy-version",
        default="local-v1",
        help="Policy version recorded in metadata-only mutation audit events",
    )

    context_audit = subparsers.add_parser(
        "context-audit-export",
        help="Export metadata-only governed-context mutation and read events as JSONL",
    )
    context_audit.add_argument("--actor", required=True, help="Configured actor ID defining organization scope")
    context_audit.add_argument("--since", help="UTC ISO-8601 lower bound (default: start of current month)")
    context_audit.add_argument("--output", default="-", help="Output path or - for stdout (default: -)")
    context_audit.add_argument("--force", action="store_true", help="Allow replacing an existing output file")

    benchmark = subparsers.add_parser(
        "context-benchmark",
        help="Measure governed retrieval quality, safety, compression, and latency",
    )
    benchmark.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS_PATH),
        help="Frozen benchmark corpus JSON (default: bundled corpus)",
    )
    benchmark.add_argument(
        "--references",
        default=str(DEFAULT_REFERENCES_PATH),
        help="Separated hidden-outcome JSON (default: bundled references)",
    )
    benchmark.add_argument(
        "--profile",
        choices=["report", "regression", "release"],
        default="report",
        help="Threshold profile; release is the strict lifecycle and retrieval gate (default: report)",
    )
    benchmark.add_argument(
        "--ci-subset",
        action="store_true",
        help="Run only the deterministic 12-task CI subset while validating the full corpus",
    )
    benchmark.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Repeated measurements per task and baseline, from 1 to 100 (default: 1)",
    )
    benchmark.add_argument("--output", default="-", help="Evidence JSON path or - for stdout (default: -)")
    benchmark.add_argument("--force", action="store_true", help="Allow replacing an existing output file")
    return parser


def _add_context_read_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor", required=True, help="Configured actor ID")
    parser.add_argument("--repository", help="Repository scope ID")
    parser.add_argument("--branch", help="Branch scope; requires --repository")
    parser.add_argument(
        "--clearance",
        choices=CLASSIFICATIONS,
        default="internal",
        help="Maximum permitted classification (default: internal)",
    )
    parser.add_argument("--as-of", help="UTC ISO-8601 evaluation time (default: now)")
    parser.add_argument(
        "--include-provisional",
        action="store_true",
        help="Include provisional records; verified-only is the default",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.command == "login":
        return _session_login_command(args)
    if args.command == "logout":
        return _session_logout_command(args)
    if args.command == "auth" and args.auth_command == "token":
        return _auth_token(
            args.env,
            gateway=args.gateway,
            gateway_env=args.gateway_env,
            profile=args.profile,
            allow_insecure_http=args.allow_insecure_http,
        )
    if args.command == "dlp":
        return _dlp_approval_command(args)
    if args.command == "sessions":
        return _session_admin_command(args)
    if args.command == "context-benchmark":
        try:
            result, exit_code = run_benchmark(
                args.corpus,
                args.references,
                profile=args.profile,
                ci_subset=args.ci_subset,
                iterations=args.iterations,
            )
            write_benchmark_result(result, args.output, force=args.force)
            return exit_code
        except ContextBenchmarkError as error:
            print(f"context benchmark error: {error}", file=sys.stderr)
            return 1
    if args.command in {"mcp", "mcp-config"}:
        try:
            if args.command == "mcp":
                return run_mcp_server(
                    base_url=args.url,
                    credential_env=args.credential_env,
                    timeout_seconds=args.timeout_seconds,
                )
            return _mcp_config(
                args.client,
                args.url,
                credential_env=args.credential_env,
                timeout_seconds=args.timeout_seconds,
            )
        except MCPConfigurationError as error:
            print(f"MCP configuration error: {error}", file=sys.stderr)
            return 2
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
        if args.command == "client-config":
            return _client_config(
                config,
                args.client,
                args.url,
                actor_id=args.actor,
                auth_mode=args.auth_mode,
                credential_env=args.credential_env,
                profile=args.profile,
            )
        if args.command == "audit-export":
            return _audit_export(config, args)
        if args.command == "context-pack":
            return _context_pack(config, args)
        if args.command == "context-import":
            return _context_import(config, args)
        if args.command == "context-snapshot-import":
            return _context_snapshot_import(config, args)
        if args.command == "context-snapshot-show":
            return _context_snapshot_show(config, args)
        if args.command == "context-list":
            return _context_list(config, args)
        if args.command == "context-export":
            return _context_export(config, args)
        if args.command == "context-delete":
            return _context_delete(config, args)
        if args.command == "context-audit-export":
            return _context_audit_export(config, args)
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except ContextError as error:
        print(f"context error: {error}", file=sys.stderr)
        return 2
    except ContextStoreError as error:
        print(f"context store error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 2


def _serve(config: GatewayConfig) -> int:
    missing = _missing_upstream_credentials(config)
    if missing:
        print(
            "warning: requests for these providers will fail until credentials are set: " + ", ".join(missing),
            file=sys.stderr,
        )
    server = GatewayServer(config)
    print(f"Hormuz listening on http://{config.listen.host}:{config.listen.port}")
    print("Protocols: POST /v1/responses, POST /v1/messages, and POST /v1/context/packs")
    print(f"Usage database: {config.database_path}")
    print(f"Context database: {config.context_database_path}")
    if config.session_broker.enabled:
        print(f"Session database: {config.session_broker.database_path}")

    def stop(_signum, _frame):
        server.shutdown()

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def _doctor(config: GatewayConfig) -> int:
    print(f"configuration: {config.source_path}")
    print(f"listener: http://{config.listen.host}:{config.listen.port}")
    print(f"actors: {len(config.identities_by_actor)}")
    print(f"static identities: {len(config.identities_by_token)}")
    print(f"OIDC issuers: {len(config.oidc_issuers)}")
    print(f"OIDC subject mappings: {len(config.identities_by_subject)}")
    print(f"human session broker: {'enabled' if config.session_broker.enabled else 'disabled'}")
    print(f"model routes: {len(config.model_routes)}")
    print(f"secret egress control: {config.secret_controls.mode}")
    print(f"DLP policy version: {config.dlp_controls.policy_version}")
    print(f"DLP rules: {len(config.dlp_controls.rules)}")
    print(f"DLP team overlays: {len(config.team_dlp_overlays)}")
    print(f"DLP actor overlays: {len(config.actor_dlp_overlays)}")
    print(
        "DLP approval: "
        + ("enabled (15-minute single-use)" if config.dlp_controls.approval.enabled else "disabled")
    )
    print(
        "DLP approvers: "
        + str(
            sum(
                "dlp_approver" in identity.capabilities
                for identity in config.identities_by_actor.values()
            )
        )
    )
    if config.dlp_controls.rules:
        action_counts: dict[str, int] = {}
        for rule in config.dlp_controls.rules:
            action_counts[rule.action] = action_counts.get(rule.action, 0) + 1
        print(
            "DLP actions: "
            + ", ".join(
                f"{action}={action_counts[action]}" for action in sorted(action_counts)
            )
        )
    print(f"usage database: {config.database_path}")
    print(f"context database: {config.context_database_path}")
    if config.oidc_issuers:
        try:
            metadata = Authenticator(config).validate_metadata()
        except AuthenticationError as error:
            print(f"OIDC metadata: unavailable ({error.code})")
            return 1
        print(f"OIDC signing keys: {sum(metadata.values())} usable across {len(metadata)} issuer(s)")
    missing = _missing_upstream_credentials(config)
    if missing:
        print("missing upstream credentials:")
        for protocol in missing:
            print(f"  - {protocol}: {config.upstreams[protocol].api_key_env}")
        return 1
    print("upstream credentials: configured")
    return 0


def _status(config: GatewayConfig, args: argparse.Namespace) -> int:
    rows = UsageStore(config.database_path).report_rows(
        group_by=args.group_by,
        actor_id=args.actor,
        team_id=args.team,
    )
    report = []
    for row in rows:
        cost_usd = row["cost_microusd"] / 1_000_000
        estimated_cost_usd = row["estimated_cost_microusd"] / 1_000_000
        budget_usd = _budget_for_scope(
            config,
            args.group_by,
            row,
            actor_filter=args.actor,
            team_filter=args.team,
        )
        report.append(
            {
                **row,
                "cost_usd": cost_usd,
                "estimated_cost_usd": estimated_cost_usd,
                "budget_usd": budget_usd,
                "budget_remaining_usd": max(0.0, budget_usd - cost_usd) if budget_usd is not None else None,
                "budget_used_percent": (cost_usd / budget_usd * 100) if budget_usd else None,
            }
        )
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    if not report:
        print("No Hormuz requests recorded this month.")
        return 0
    print(
        "SCOPE_ID\tSCOPE_NAME\tTEAM\tPROVIDER\tCLIENT\tREQUESTS\tSUCCEEDED\tFAILED\tDENIED\t"
        "INPUT\tOUTPUT\tCACHE_READ\tCACHE_WRITE\tREASONING\tTOTAL\tCOST_USD\tBUDGET_USD\t"
        "REMAINING_USD\tBUDGET_USED_PCT\tACTORS\tREDACTIONS\tBILLABLE\tESTIMATED_COST_USD\t"
        "UNPRICED\tCOST_BASES\tRATE_CARD_VERSIONS"
    )
    for row in report:
        print(
            f"{row['scope_id']}\t{row['scope_name']}\t{row.get('team_name', '-')}\t"
            f"{row.get('protocol', '-')}\t{row.get('client', '-')}\t{row['requests']}\t"
            f"{row['succeeded']}\t{row['failed']}\t{row['denied']}\t{row['input_tokens']}\t"
            f"{row['output_tokens']}\t{row['cache_read_tokens']}\t{row['cache_write_tokens']}\t"
            f"{row['reasoning_tokens']}\t{row['total_tokens']}\t{row['cost_usd']:.6f}\t"
            f"{_display_number(row['budget_usd'])}\t{_display_number(row['budget_remaining_usd'])}\t"
            f"{_display_number(row['budget_used_percent'])}\t{row['active_actors']}\t{row['redactions']}\t"
            f"{row['billable_tokens']}\t{row['estimated_cost_usd']:.6f}\t{row['unpriced_requests']}\t"
            f"{','.join(row['cost_bases'])}\t{','.join(row['rate_card_versions'])}"
        )
    return 0


def _budget_for_scope(
    config: GatewayConfig,
    group_by: str,
    row: dict[str, object],
    *,
    actor_filter: str | None = None,
    team_filter: str | None = None,
) -> float | None:
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


def _display_number(value: object) -> str:
    return "-" if value is None else f"{float(value):.2f}"


def _policy_check(config: GatewayConfig, args: argparse.Namespace) -> int:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        print(f"unknown actor: {args.actor}", file=sys.stderr)
        return 2
    store = UsageStore(config.database_path)
    decision = PolicyEngine(config, store).evaluate(
        identity=identity,
        client=args.client,
        protocol=args.protocol,
        requested_model=args.model,
        requested_output_tokens=args.max_output_tokens,
    )
    effective_dlp = (
        config.resolved_dlp_controls(
            identity,
            protocol=decision.route.protocol,
            model=decision.route.upstream_model,
        )
        if decision.route is not None
        else None
    )
    print(
        json.dumps(
            {
                "allowed": decision.allowed,
                "action": decision.action,
                "reason": decision.reason,
                "requested_model": decision.requested_model,
                "resolved_alias": decision.resolved_alias,
                "upstream_model": decision.route.upstream_model if decision.route else None,
                "max_output_tokens": decision.max_output_tokens,
                "dlp_policy_version": (
                    effective_dlp.policy_version if effective_dlp is not None else None
                ),
                "dlp_rules": (
                    [
                        {
                            "rule_id": rule.rule_id,
                            "action": rule.action,
                            "providers": list(rule.providers),
                            "models": list(rule.models),
                        }
                        for rule in effective_dlp.rules
                    ]
                    if effective_dlp is not None
                    else []
                ),
            },
            indent=2,
        )
    )
    return 0 if decision.allowed else 3


def _client_config(
    config: GatewayConfig,
    client: str,
    url: str | None,
    *,
    actor_id: str | None = None,
    auth_mode: str = "auto",
    credential_env: str | None = None,
    profile: str | None = None,
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
    uses_session = False
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
    elif auth_mode == "session":
        if not config.session_broker.enabled:
            raise ConfigError("Human session broker is not enabled")
        if oidc_identity is None:
            raise ConfigError(f"Actor {identity.actor_id} has no OIDC subject mapping")
        selected_identity = oidc_identity
        uses_oidc = False
        uses_session = True
    elif static_identity is not None:
        selected_identity = static_identity
        uses_oidc = False
    elif oidc_identity is not None:
        selected_identity = oidc_identity
        uses_oidc = True
    else:  # pragma: no cover - configuration requires at least one source
        raise ConfigError(f"Actor {identity.actor_id} has no authentication source")
    policy_client = "codex" if client == "codex" else "claude-code"
    if selected_identity.allowed_clients and policy_client not in selected_identity.allowed_clients:
        raise ConfigError(
            f"Identity {selected_identity.actor_id} is not authorized to use {policy_client}"
        )
    env_name = credential_env or (
        "HORMUZ_OIDC_ACCESS_TOKEN" if uses_oidc else selected_identity.token_env
    )
    if not uses_session and (
        not env_name or not env_name.replace("_", "A").isalnum() or env_name[0].isdigit()
    ):
        raise ConfigError("credential environment variable must contain only letters, digits, and underscores")
    session_profile = profile or client
    try:
        validate_profile(session_profile)
    except CredentialStoreError as error:
        raise ConfigError("session profile must use only letters, digits, dots, dashes, and underscores") from error
    if client == "codex":
        policy = config.resolved_policy(selected_identity)
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
        if uses_session:
            helper_args = [
                "auth",
                "token",
                "--gateway",
                base_url,
                "--profile",
                session_profile,
            ]
            if base_url.startswith("http://"):
                helper_args.append("--allow-insecure-http")
            lines.extend(
                [
                    "",
                    "[model_providers.hormuz.auth]",
                    'command = "hormuz"',
                    f"args = {json.dumps(helper_args)}",
                    "refresh_interval_ms = 300000",
                ]
            )
        elif uses_oidc:
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
        if uses_session:
            helper_parts = [
                "hormuz", "auth", "token", "--gateway-env",
                "HORMUZ_SESSION_GATEWAY", "--profile", session_profile,
            ]
            if base_url.startswith("http://"):
                helper_parts.append("--allow-insecure-http")
            print("# Put this JSON in the managed or user Claude Code settings file:")
            print(
                json.dumps(
                    {
                        "apiKeyHelper": " ".join(helper_parts),
                        "env": {
                            "ANTHROPIC_BASE_URL": base_url,
                            "HORMUZ_SESSION_GATEWAY": base_url,
                            "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "300000",
                        },
                    },
                    indent=2,
                )
            )
        elif uses_oidc:
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


def _auth_token(
    env_name: str | None,
    *,
    gateway: str | None = None,
    gateway_env: str | None = None,
    profile: str = "default",
    allow_insecure_http: bool = False,
) -> int:
    selected_sources = sum(value is not None for value in (gateway, gateway_env, env_name))
    if selected_sources > 1:
        print("choose only one of --gateway, --gateway-env, or --env", file=sys.stderr)
        return 2
    if gateway_env is not None:
        if (
            not gateway_env
            or not gateway_env.replace("_", "A").isalnum()
            or gateway_env[0].isdigit()
        ):
            print("gateway environment variable name is invalid", file=sys.stderr)
            return 2
        gateway = os.environ.get(gateway_env)
        if not gateway:
            print(f"gateway environment variable is not set: {gateway_env}", file=sys.stderr)
            return 1
    if gateway is not None:
        try:
            value = session_access_token(
                gateway=gateway,
                profile=profile,
                allow_insecure_http=allow_insecure_http,
            )
        except (SessionClientError, CredentialStoreError) as error:
            print(f"session credential unavailable: {error.code}", file=sys.stderr)
            return 1
        print(value)
        return 0
    selected_env = env_name or "HORMUZ_OIDC_ACCESS_TOKEN"
    value = os.environ.get(selected_env, "")
    if not value:
        print(f"credential environment variable is not set: {selected_env}", file=sys.stderr)
        return 1
    if len(value.encode("utf-8")) > 64 * 1024 or "\n" in value or "\r" in value:
        print(f"credential environment variable is invalid: {selected_env}", file=sys.stderr)
        return 1
    print(value)
    return 0


def _dlp_approval_command(args: argparse.Namespace) -> int:
    if args.dlp_command != "approval" or args.dlp_approval_command not in {
        "show",
        "approve",
    }:
        return 2
    try:
        if args.profile is not None:
            credential = session_access_token(
                gateway=args.gateway,
                profile=args.profile,
                allow_insecure_http=args.allow_insecure_http,
            )
        else:
            env_name = args.credential_env or "HORMUZ_TOKEN"
            if (
                not env_name
                or not env_name.replace("_", "A").isalnum()
                or env_name[0].isdigit()
            ):
                print("credential environment variable name is invalid", file=sys.stderr)
                return 2
            credential = os.environ.get(env_name, "")
            if not credential:
                print(f"credential environment variable is not set: {env_name}", file=sys.stderr)
                return 1
        client = DLPApprovalClient(
            args.gateway,
            credential=credential,
            allow_insecure_http=args.allow_insecure_http,
        )
        if args.dlp_approval_command == "show":
            result = client.show(args.request_id)
        else:
            result = client.approve(args.request_id)
    except (DLPApprovalClientError, SessionClientError, CredentialStoreError) as error:
        print(f"DLP approval failed: {error.code}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _session_admin_command(args: argparse.Namespace) -> int:
    try:
        if args.profile is not None:
            credential = session_access_token(
                gateway=args.gateway,
                profile=args.profile,
                allow_insecure_http=args.allow_insecure_http,
            )
        else:
            env_name = args.credential_env or "HORMUZ_TOKEN"
            if (
                not env_name
                or not env_name.replace("_", "A").isalnum()
                or env_name[0].isdigit()
            ):
                print("credential environment variable name is invalid", file=sys.stderr)
                return 2
            credential = os.environ.get(env_name, "")
            if not credential:
                print(f"credential environment variable is not set: {env_name}", file=sys.stderr)
                return 1
        client = SessionAdminClient(
            args.gateway,
            credential=credential,
            allow_insecure_http=args.allow_insecure_http,
        )
        if args.sessions_command == "list":
            result = client.list_sessions(
                actor_id=args.actor,
                team_id=args.team,
                limit=args.limit,
                cursor=args.cursor,
            )
        elif args.sessions_command == "revoke":
            if (args.scope == "organization") != (args.target is None):
                print(
                    "--target is required except for organization scope",
                    file=sys.stderr,
                )
                return 2
            result = client.revoke(
                scope=args.scope,
                target=args.target,
                reason_code=args.reason,
            )
        elif args.sessions_command == "events":
            result = client.list_events(
                actor_id=args.actor,
                team_id=args.team,
                event_type=args.event_type,
                since=args.since,
                limit=args.limit,
                cursor=args.cursor,
            )
        else:
            return 2
    except (
        SessionAdminClientError,
        SessionClientError,
        CredentialStoreError,
    ) as error:
        print(f"session administration failed: {error.code}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _session_login_command(args: argparse.Namespace) -> int:
    if not 30 <= args.wait_seconds <= 600:
        print("--wait-seconds must be between 30 and 600", file=sys.stderr)
        return 2
    try:
        access_expiry = session_login(
            gateway=args.gateway,
            profile=args.profile,
            client=args.client,
            issuer=args.issuer,
            no_open=args.no_open,
            allow_insecure_http=args.allow_insecure_http,
            wait_seconds=args.wait_seconds,
        )
    except (SessionClientError, CredentialStoreError) as error:
        print(f"login failed: {error.code}", file=sys.stderr)
        return 1
    print(f"Login saved securely for profile {args.profile}; access credential expires {access_expiry}")
    return 0


def _session_logout_command(args: argparse.Namespace) -> int:
    try:
        revoked = session_logout(
            gateway=args.gateway,
            profile=args.profile,
            allow_insecure_http=args.allow_insecure_http,
        )
    except (SessionClientError, CredentialStoreError) as error:
        print(f"logout failed: {error.code}", file=sys.stderr)
        return 1
    print("Session revoked and removed." if revoked else "No saved session for that profile.")
    return 0


def _mcp_config(
    client: str,
    url: str,
    *,
    credential_env: str = "HORMUZ_TOKEN",
    timeout_seconds: int = 30,
) -> int:
    base_url = validate_gateway_url(url)
    env_name = validate_credential_env(credential_env)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 60:
        raise MCPConfigurationError("MCP timeout must be between 1 and 60 seconds")
    args = [
        "mcp",
        "--url",
        base_url,
        "--credential-env",
        env_name,
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    if client == "codex":
        print("[mcp_servers.hormuz]")
        print('command = "hormuz"')
        print("args = " + json.dumps(args))
        print("env_vars = " + json.dumps([env_name]))
        print("startup_timeout_sec = 10")
        print(f"tool_timeout_sec = {timeout_seconds + 5}")
        print("required = true")
    else:
        print(
            json.dumps(
                {
                    "mcpServers": {
                        "hormuz": {
                            "type": "stdio",
                            "command": "hormuz",
                            "args": args,
                            "env": {env_name: "${" + env_name + "}"},
                        }
                    }
                },
                indent=2,
            )
        )
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
    events = UsageStore(config.database_path).audit_events(since=since, kind=args.kind)
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


def _audit_since(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _context_pack(config: GatewayConfig, args: argparse.Namespace) -> int:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        print(f"unknown actor: {args.actor}", file=sys.stderr)
        return 2
    if args.organization != identity.organization_id:
        print("context error: requested organization does not match the actor identity", file=sys.stderr)
        return 2
    if CLASSIFICATIONS.index(args.clearance) > CLASSIFICATIONS.index(identity.clearance):
        print("context error: requested clearance exceeds the actor identity", file=sys.stderr)
        return 2
    if args.branch and not args.repository:
        print("context error: --branch requires --repository", file=sys.stderr)
        return 2
    try:
        as_of = _context_as_of(args.as_of)
        principal = ContextPrincipal(
            organization_id=identity.organization_id,
            team_id=identity.team_id,
            actor_id=identity.actor_id,
            clearance=args.clearance,
            repository_id=args.repository,
            branch=args.branch,
        )
        repository: SQLiteContextRepository | None = None
        lifecycle_snapshot: ContextLifecycleSnapshot | None = None
        if args.records:
            records = _load_context_records(Path(args.records))
            if args.snapshot_file:
                scope, lifecycle_snapshot = _load_context_lifecycle_envelope(
                    Path(args.snapshot_file)
                )
                _require_lifecycle_scope(scope, identity, args.repository, args.branch)
        else:
            if args.snapshot_file:
                raise ContextError("--snapshot-file is only valid with --records")
            repository = SQLiteContextRepository(config.context_database_path)
            stored = repository.list_access_authorized(principal)
            records = [item.record for item in stored]
            if args.repository is not None and args.branch is not None:
                stored_snapshot = repository.get_lifecycle_snapshot(
                    organization_id=identity.organization_id,
                    repository_id=args.repository,
                    branch=args.branch,
                )
                if stored_snapshot is not None:
                    lifecycle_snapshot = stored_snapshot.snapshot
        pack = build_context_pack(
            records,
            ContextPackRequest(
                query=args.query,
                principal=principal,
                token_budget=args.token_budget,
                max_items=args.max_items,
                policy_version=args.policy_version,
                include_provisional=args.include_provisional,
                as_of=as_of,
            ),
            lifecycle_snapshot=lifecycle_snapshot,
        )
        if repository is not None:
            repository.record_pack_read(pack)
    except OSError as error:
        print(f"context error: cannot read {args.records}: {error}", file=sys.stderr)
        return 2
    except (ContextError, ContextStoreError) as error:
        print(f"context pack failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(pack.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _context_import(config: GatewayConfig, args: argparse.Namespace) -> int:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        print(f"unknown actor: {args.actor}", file=sys.stderr)
        return 2
    try:
        records = _order_context_records(_load_context_records(Path(args.records)))
        for record in records:
            _require_mutation_scope(record, identity)
        repository = SQLiteContextRepository(config.context_database_path)
        results = repository.ingest_many(
            records,
            actor_id=identity.actor_id,
            policy_version=args.policy_version,
        )
        created = sum(result.created for result in results)
        existing = len(results) - created
        versions = {
            result.stored.record.record_id: result.stored.version
            for result in results
        }
    except OSError as error:
        print(f"context error: cannot read {args.records}: {error}", file=sys.stderr)
        return 2
    except (ContextError, ContextStoreError) as error:
        print(f"context import failed: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "imported": created,
                "already_present": existing,
                "records": len(records),
                "versions": versions,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _context_snapshot_import(config: GatewayConfig, args: argparse.Namespace) -> int:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        print(f"unknown actor: {args.actor}", file=sys.stderr)
        return 2
    try:
        scope, snapshot = _load_context_lifecycle_envelope(Path(args.snapshot))
        _require_lifecycle_scope(
            scope,
            identity,
            str(scope["repository_id"]),
            str(scope["branch"]),
        )
        stored = SQLiteContextRepository(
            config.context_database_path
        ).observe_lifecycle_snapshot(
            organization_id=identity.organization_id,
            repository_id=str(scope["repository_id"]),
            branch=str(scope["branch"]),
            snapshot=snapshot,
            expected_version=args.expected_version,
            actor_id=identity.actor_id,
            policy_version=args.policy_version,
        )
    except OSError as error:
        print(f"context snapshot import failed: {error}", file=sys.stderr)
        return 2
    except (ContextError, ContextStoreError) as error:
        print(f"context snapshot import failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(stored.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def _context_snapshot_show(config: GatewayConfig, args: argparse.Namespace) -> int:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        print(f"unknown actor: {args.actor}", file=sys.stderr)
        return 2
    try:
        stored = SQLiteContextRepository(config.context_database_path).get_lifecycle_snapshot(
            organization_id=identity.organization_id,
            repository_id=args.repository,
            branch=args.branch,
        )
    except ContextStoreError as error:
        print(f"context snapshot lookup failed: {error}", file=sys.stderr)
        return 2
    if stored is None:
        print("context snapshot not found", file=sys.stderr)
        return 2
    print(json.dumps(stored.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


def _context_list(config: GatewayConfig, args: argparse.Namespace) -> int:
    try:
        principal = _context_principal(config, args)
        records = SQLiteContextRepository(config.context_database_path).list_authorized(
            principal,
            as_of=_context_as_of(args.as_of),
            include_provisional=args.include_provisional,
        )
    except (ContextError, ContextStoreError) as error:
        print(f"context list failed: {error}", file=sys.stderr)
        return 2
    values = [_stored_context_dict(item, include_content=args.include_content) for item in records]
    print(json.dumps({"records": values, "total": len(values)}, indent=2, ensure_ascii=False))
    return 0


def _context_export(config: GatewayConfig, args: argparse.Namespace) -> int:
    try:
        principal = _context_principal(config, args)
        records = SQLiteContextRepository(config.context_database_path).list_authorized(
            principal,
            as_of=_context_as_of(args.as_of),
            include_provisional=args.include_provisional,
        )
        values = [item.record.to_dict() for item in records]
    except (ContextError, ContextStoreError) as error:
        print(f"context export failed: {error}", file=sys.stderr)
        return 2
    result = _write_private_jsonl(
        values,
        output=args.output,
        force=args.force,
        label="context export",
    )
    if result is None:
        return 2
    count, digest, destination = result
    print(
        f"exported {count} context records to {destination}; sha256={digest}",
        file=sys.stderr,
    )
    return 0


def _context_delete(config: GatewayConfig, args: argparse.Namespace) -> int:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        print(f"unknown actor: {args.actor}", file=sys.stderr)
        return 2
    try:
        SQLiteContextRepository(config.context_database_path).delete(
            organization_id=identity.organization_id,
            record_id=args.record_id,
            expected_version=args.expected_version,
            actor_id=identity.actor_id,
            policy_version=args.policy_version,
        )
    except (ContextError, ContextStoreError) as error:
        print(f"context delete failed: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "deleted": args.record_id,
                "organization_id": identity.organization_id,
                "prior_version": args.expected_version,
            },
            sort_keys=True,
        )
    )
    return 0


def _context_audit_export(config: GatewayConfig, args: argparse.Namespace) -> int:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        print(f"unknown actor: {args.actor}", file=sys.stderr)
        return 2
    try:
        since = _context_audit_since(args.since)
        values = SQLiteContextRepository(config.context_database_path).audit_events(
            organization_id=identity.organization_id,
            since=since,
        )
    except (ContextError, ContextStoreError) as error:
        print(f"context audit export failed: {error}", file=sys.stderr)
        return 2
    result = _write_private_jsonl(
        values,
        output=args.output,
        force=args.force,
        label="context audit export",
    )
    if result is None:
        return 2
    count, digest, destination = result
    print(
        f"exported {count} context audit events to {destination}; sha256={digest}",
        file=sys.stderr,
    )
    return 0


def _context_audit_since(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContextError("--since must be a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ContextError("--since must include a timezone")
    return parsed.astimezone(timezone.utc)


def _context_principal(config: GatewayConfig, args: argparse.Namespace) -> ContextPrincipal:
    identity = config.identities_by_actor.get(args.actor)
    if identity is None:
        raise ContextError(f"unknown actor: {args.actor}")
    if CLASSIFICATIONS.index(args.clearance) > CLASSIFICATIONS.index(identity.clearance):
        raise ContextError("requested clearance exceeds the actor identity")
    if args.branch and not args.repository:
        raise ContextError("--branch requires --repository")
    return ContextPrincipal(
        organization_id=identity.organization_id,
        team_id=identity.team_id,
        actor_id=identity.actor_id,
        clearance=args.clearance,
        repository_id=args.repository,
        branch=args.branch,
    )


def _require_mutation_scope(record: ContextRecord, identity: Identity) -> None:
    if record.organization_id != identity.organization_id:
        raise ContextError(
            f"record {record.record_id} organization does not match the actor identity"
        )
    expected_scope = {
        "organization": identity.organization_id,
        "team": identity.team_id,
        "actor": identity.actor_id,
    }[record.visibility]
    if record.scope_id != expected_scope:
        raise ContextError(f"record {record.record_id} scope exceeds the actor identity")
    if CLASSIFICATIONS.index(record.classification) > CLASSIFICATIONS.index(identity.clearance):
        raise ContextError(f"record {record.record_id} classification exceeds the actor identity")


def _order_context_records(records: list[ContextRecord]) -> list[ContextRecord]:
    by_id: dict[str, ContextRecord] = {}
    for record in records:
        if record.record_id in by_id:
            raise ContextError(f"duplicate context record id: {record.record_id}")
        by_id[record.record_id] = record
    ordered: list[ContextRecord] = []
    active: set[str] = set()
    complete: set[str] = set()

    def visit(record: ContextRecord) -> None:
        if record.record_id in complete:
            return
        if record.record_id in active:
            raise ContextError(f"context supersession cycle includes: {record.record_id}")
        active.add(record.record_id)
        if record.supersedes_id in by_id:
            visit(by_id[record.supersedes_id])
        active.remove(record.record_id)
        complete.add(record.record_id)
        ordered.append(record)

    for record in records:
        visit(record)
    return ordered


def _stored_context_dict(
    stored: StoredContextRecord,
    *,
    include_content: bool,
) -> dict[str, object]:
    value = stored.to_dict()
    if not include_content:
        value.pop("content", None)
    return value


def _write_private_jsonl(
    values: list[dict[str, object]],
    *,
    output: str,
    force: bool,
    label: str,
) -> tuple[int, str, str] | None:
    stream = sys.stdout
    should_close = False
    output_path: Path | None = None
    if output != "-":
        output_path = Path(output).expanduser().absolute()
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | (os.O_TRUNC if force else os.O_EXCL)
        )
        try:
            descriptor = os.open(output_path, flags, 0o600)
        except FileExistsError:
            print(f"{label} already exists: {output_path} (use --force to replace it)", file=sys.stderr)
            return None
        except OSError as error:
            print(f"cannot open {label} {output_path}: {error}", file=sys.stderr)
            return None
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows permission semantics
            os.chmod(output_path, 0o600)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        should_close = True
    digest = hashlib.sha256()
    try:
        for value in values:
            line = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            stream.write(line)
            digest.update(line.encode("utf-8"))
        stream.flush()
        if should_close:
            os.fsync(stream.fileno())
    finally:
        if should_close:
            stream.close()
    return len(values), digest.hexdigest(), str(output_path) if output_path is not None else "stdout"


def _load_context_records(path: Path) -> list[ContextRecord]:
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ContextError("context record input cannot exceed 25 MiB")
    records: list[ContextRecord] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ContextError(f"invalid JSON on context record line {line_number}: {error.msg}") from error
            if not isinstance(value, dict):
                raise ContextError(f"context record line {line_number} must be a JSON object")
            try:
                records.append(ContextRecord.from_dict(value))
            except ContextError as error:
                raise ContextError(f"context record line {line_number}: {error}") from error
    return records


def _load_context_lifecycle_envelope(
    path: Path,
) -> tuple[dict[str, str], ContextLifecycleSnapshot]:
    if path.stat().st_size > 1024 * 1024:
        raise ContextError("context lifecycle snapshot cannot exceed 1 MiB")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ContextError("context lifecycle snapshot must be valid JSON") from error
    if not isinstance(value, dict):
        raise ContextError("context lifecycle snapshot envelope must be an object")
    fields = {
        "schema_version",
        "organization_id",
        "repository_id",
        "branch",
        "snapshot",
    }
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContextError(f"unknown context lifecycle envelope fields: {', '.join(unknown)}")
    if missing:
        raise ContextError(f"missing context lifecycle envelope fields: {', '.join(missing)}")
    if value.get("schema_version") != "hormuz.context-lifecycle-envelope.v1":
        raise ContextError("unsupported context lifecycle envelope schema_version")
    scope: dict[str, str] = {}
    for name in ("organization_id", "repository_id", "branch"):
        item = value.get(name)
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item.encode("utf-8")) > 512
            or any(character in item for character in ("\n", "\r", "\x00"))
        ):
            raise ContextError(f"context lifecycle {name} must be a bounded non-empty string")
        scope[name] = item
    return scope, ContextLifecycleSnapshot.from_dict(value.get("snapshot"))


def _require_lifecycle_scope(
    scope: dict[str, str],
    identity: Identity,
    repository_id: str | None,
    branch: str | None,
) -> None:
    if scope["organization_id"] != identity.organization_id:
        raise ContextError("context lifecycle organization exceeds the actor identity")
    if repository_id is None or branch is None:
        raise ContextError("context lifecycle evaluation requires repository and branch scope")
    if scope["repository_id"] != repository_id or scope["branch"] != branch:
        raise ContextError("context lifecycle snapshot does not match the requested repository scope")


def _context_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContextError("--as-of must be a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ContextError("--as-of must include a timezone")
    return parsed.astimezone(timezone.utc)


def _missing_upstream_credentials(config: GatewayConfig) -> list[str]:
    return [
        protocol
        for protocol, upstream in config.upstreams.items()
        if not os.environ.get(upstream.api_key_env)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
