"""CLI-first registry administration through the same authorized wire service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

from ..auth import AuthenticationError, Authenticator
from ..portfolio_config import authorize
from ..portfolio_repository import create_portfolio_repository
from ..portfolio_service import PortfolioService
from ..portfolio_wire import ATTRIBUTIONS, BINDINGS, SCOPES, PortfolioError, REQUEST_BYTES, canonical
from ..store_router import create_repository_bundle


def add_portfolio_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("portfolio", help="Administer the tenant-scoped v1.1 registry")
    commands = parser.add_subparsers(dest="portfolio_command", required=True)
    for name in ("create", "version", "archive", "tombstone", "show", "list", "bind", "bindings", "attribute", "attributions"):
        command = commands.add_parser(name)
        command.add_argument("--token-env", default="HORMUZ_PORTFOLIO_TOKEN", help="Environment variable holding an existing administrator bearer token")
        if name in {"version", "archive", "tombstone", "show"}:
            command.add_argument("work_scope_id")
        if name in {"create", "version", "bind", "attribute"}:
            command.add_argument("file", help="Strict version-1 JSON mutation request")
        if name in {"create", "version", "archive", "tombstone", "bind", "attribute"}:
            command.add_argument("--idempotency-key", required=True)
        if name in {"archive", "tombstone"}:
            command.add_argument("--expected-version", required=True, type=int)
        if name == "show":
            command.add_argument("--version", type=int)
        if name in {"list", "bindings", "attributions"}:
            command.add_argument("--limit", type=int)
            command.add_argument("--cursor")
            command.add_argument("--start-at")
            command.add_argument("--end-at")
            command.add_argument("--work-scope-id")
            if name == "bindings":
                command.add_argument("--connector-id")


def run(config, args) -> int:
    try:
        token = os.environ.get(args.token_env, "")
        authenticator = Authenticator(config)
        try:
            identity = authenticator.authenticate(token)
        except AuthenticationError:
            raise PortfolioError("unauthenticated") from None
        principal = authorize(config.portfolio_control, identity)
        # Even local initialization/migration follows authorization. No caller
        # can use a denied portfolio command to open or create the database.
        repositories = create_repository_bundle(config, portfolio_factory=create_portfolio_repository)
        service = PortfolioService(config, repositories.portfolio, authenticator)
        name = args.portfolio_command
        method = "GET" if name in {"list", "bindings", "show", "attributions"} else "POST"
        path = ATTRIBUTIONS if name in {"attribute", "attributions"} else BINDINGS if name in {"bind", "bindings"} else SCOPES
        if name == "show":
            path += "/" + args.work_scope_id
        elif name in {"version", "archive", "tombstone"}:
            path += "/" + args.work_scope_id + "/versions"
        query = urlencode({key: getattr(args, key) for key in
                           ("limit", "cursor", "start_at", "end_at", "work_scope_id", "connector_id", "version")
                           if getattr(args, key, None) is not None and not (key == "work_scope_id" and name == "show")}) if method == "GET" else ""
        data = b""
        if name in {"archive", "tombstone"}:
            _, prior = service.dispatch_authorized(
                principal, "GET", SCOPES + "/" + args.work_scope_id,
                query=urlencode({"version": args.expected_version}),
            )
            state = "archived" if name == "archive" else "tombstoned"
            data = canonical({
                "schema_id": "hormuz.work-scope-version-request", "schema_version": 1,
                "expected_version": args.expected_version,
                "parent_work_scope_id": prior["parent"]["work_scope_id"] if prior["parent"] else None,
                "owner_team_id": prior["owner_team_id"],
                "display_name": prior["display_name"] if state == "archived" else None,
                "state": state, "reason_code": state,
            }).encode("utf-8")
        elif method == "POST":
            try:
                with Path(args.file).open("rb") as source:
                    data = source.read(REQUEST_BYTES + 1)
            except OSError:
                raise PortfolioError("invalid_request") from None
        _, result = service.dispatch_authorized(
            principal, method, path, query=query, body=data, idempotency_key=getattr(args, "idempotency_key", None),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except PortfolioError as error:
        print(json.dumps(error.envelope(), sort_keys=True), file=sys.stderr)
        return 2
