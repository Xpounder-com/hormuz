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
    ContextPackRequest,
    ContextPrincipal,
    ContextRecord,
    build_context_pack,
)
from .context_store import ContextStoreError, SQLiteContextRepository, StoredContextRecord
from .policy import PolicyEngine
from .server import GatewayServer
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

    context = subparsers.add_parser(
        "context-pack",
        help="Build an explicit governed context pack from the repository or JSONL",
    )
    context.add_argument(
        "--records",
        help="Compatibility path to content-bearing context JSONL; defaults to the repository",
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
    if args.command == "auth" and args.auth_command == "token":
        return _auth_token(args.env)
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
            )
        if args.command == "audit-export":
            return _audit_export(config, args)
        if args.command == "context-pack":
            return _context_pack(config, args)
        if args.command == "context-import":
            return _context_import(config, args)
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
    print(f"model routes: {len(config.model_routes)}")
    print(f"secret egress control: {config.secret_controls.mode}")
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
        "REMAINING_USD\tBUDGET_USED_PCT\tACTORS\tREDACTIONS"
    )
    for row in report:
        print(
            f"{row['scope_id']}\t{row['scope_name']}\t{row.get('team_name', '-')}\t"
            f"{row.get('protocol', '-')}\t{row.get('client', '-')}\t{row['requests']}\t"
            f"{row['succeeded']}\t{row['failed']}\t{row['denied']}\t{row['input_tokens']}\t"
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
        if args.records:
            records = _load_context_records(Path(args.records))
        else:
            repository = SQLiteContextRepository(config.context_database_path)
            stored = repository.list_authorized(
                principal,
                as_of=as_of,
                include_provisional=args.include_provisional,
            )
            records = [item.record for item in stored]
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
