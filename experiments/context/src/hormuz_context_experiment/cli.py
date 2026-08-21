from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from hormuz.config import ConfigError, GatewayConfig

from .context import (
    CLASSIFICATIONS,
    ContextError,
    ContextPackRequest,
    ContextPrincipal,
    ContextRecord,
    build_context_pack,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hormuz-context-experiment",
        description="Deprecated experimental context-pack tooling, separate from the Hormuz core gateway.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("HORMUZ_CONFIG", "hormuz.json"),
        help="Path to a compatible Hormuz JSON configuration (default: hormuz.json or HORMUZ_CONFIG)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Retained for command-line compatibility; the experiment emits no request logs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    context = subparsers.add_parser(
        "context-pack",
        help="Build an explicit experimental context pack from JSONL records",
    )
    context.add_argument("--records", required=True, help="Path to content-bearing context JSONL")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = GatewayConfig.load(args.config)
        return _context_pack(config, args)
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    except ContextError as error:
        print(f"context error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


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
        records = _load_context_records(Path(args.records))
        pack = build_context_pack(
            records,
            ContextPackRequest(
                query=args.query,
                principal=ContextPrincipal(
                    organization_id=identity.organization_id,
                    team_id=identity.team_id,
                    actor_id=identity.actor_id,
                    clearance=args.clearance,
                    repository_id=args.repository,
                    branch=args.branch,
                ),
                token_budget=args.token_budget,
                max_items=args.max_items,
                policy_version=args.policy_version,
                include_provisional=args.include_provisional,
                as_of=as_of,
            ),
        )
    except OSError as error:
        print(f"context error: cannot read {args.records}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(pack.to_dict(), indent=2, ensure_ascii=False))
    return 0


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
