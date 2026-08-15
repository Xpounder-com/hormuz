from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path

from .config import ConfigError, GatewayConfig
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
    status = subparsers.add_parser("status", help="Print current-month usage by identity")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    policy = subparsers.add_parser("policy-check", help="Evaluate a request without sending it upstream")
    policy.add_argument("--actor", required=True, help="Configured actor ID")
    policy.add_argument("--client", required=True, choices=["codex", "claude-code"])
    policy.add_argument("--protocol", required=True, choices=["openai", "anthropic"])
    policy.add_argument("--model", required=True, help="Company model alias")
    policy.add_argument("--max-output-tokens", type=int)

    connect = subparsers.add_parser("client-config", help="Print client configuration for this gateway")
    connect.add_argument("client", choices=["codex", "claude"])
    connect.add_argument("--url", help="Externally reachable gateway URL; defaults to configured listener")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = GatewayConfig.load(args.config)
        if args.command == "serve":
            return _serve(config)
        if args.command == "doctor":
            return _doctor(config)
        if args.command == "status":
            return _status(config, as_json=args.json)
        if args.command == "policy-check":
            return _policy_check(config, args)
        if args.command == "client-config":
            return _client_config(config, args.client, args.url)
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
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
    print("Protocols: POST /v1/responses and POST /v1/messages")
    print(f"Usage database: {config.database_path}")

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
    print(f"identities: {len(config.identities_by_token)}")
    print(f"model routes: {len(config.model_routes)}")
    print(f"usage database: {config.database_path}")
    missing = _missing_upstream_credentials(config)
    if missing:
        print("missing upstream credentials:")
        for protocol in missing:
            print(f"  - {protocol}: {config.upstreams[protocol].api_key_env}")
        return 1
    print("upstream credentials: configured")
    return 0


def _status(config: GatewayConfig, *, as_json: bool) -> int:
    rows = UsageStore(config.database_path).summary_rows()
    if as_json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No Hormuz requests recorded this month.")
        return 0
    print("ACTOR\tTEAM\tCLIENT\tPROTOCOL\tREQUESTS\tTOKENS\tCOST_USD\tDENIED")
    for row in rows:
        print(
            f"{row['actor_name']}\t{row['team_name']}\t{row['client']}\t{row['protocol']}\t"
            f"{row['requests']}\t{row['tokens']}\t{row['cost_microusd'] / 1_000_000:.6f}\t{row['denied']}"
        )
    return 0


def _policy_check(config: GatewayConfig, args: argparse.Namespace) -> int:
    identity = next((item for item in config.identities_by_token.values() if item.actor_id == args.actor), None)
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


def _client_config(config: GatewayConfig, client: str, url: str | None) -> int:
    base_url = (url or f"http://{config.listen.host}:{config.listen.port}").rstrip("/")
    identity = next(iter(config.identities_by_token.values()))
    if client == "codex":
        policy = config.resolved_policy(identity)
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
            raise ConfigError(f"Identity {identity.actor_id} has no allowed OpenAI model for Codex")
        print(
            f'''# Put this in the user-level ~/.codex/config.toml\nmodel = "{default_model}"\nmodel_provider = "hormuz"\n\n[model_providers.hormuz]\nname = "Hormuz"\nbase_url = "{base_url}/v1"\nenv_key = "{identity.token_env}"\nwire_api = "responses"'''
        )
    else:
        print(f'export ANTHROPIC_BASE_URL="{base_url}"')
        print(f'export ANTHROPIC_AUTH_TOKEN="${{{identity.token_env}}}"')
        print("claude")
    return 0


def _missing_upstream_credentials(config: GatewayConfig) -> list[str]:
    return [
        protocol
        for protocol, upstream in config.upstreams.items()
        if not os.environ.get(upstream.api_key_env)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
