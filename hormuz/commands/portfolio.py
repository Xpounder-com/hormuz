"""CLI-first registry administration through the same authorized wire service."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

from ..auth import AuthenticationError, Authenticator
from ..portfolio_config import authorize
from ..portfolio_repository import create_portfolio_repository
from ..portfolio_service import PortfolioService
from ..portfolio_wire import (
    ATTRIBUTIONS,
    BINDINGS,
    OUTCOMES,
    ROLE_VIEWS,
    SCOPES,
    PortfolioError,
    REQUEST_BYTES,
    canonical,
)
from ..store_router import create_repository_bundle


def add_portfolio_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("portfolio", help="Administer the tenant-scoped v1.1 registry")
    commands = parser.add_subparsers(dest="portfolio_command", required=True)
    for name in ("create", "version", "archive", "tombstone", "show", "list", "bind", "bindings", "attribute", "attributions", "outcomes"):
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
        if name in {"list", "bindings", "attributions", "outcomes"}:
            command.add_argument("--limit", type=int)
            command.add_argument("--cursor")
            command.add_argument("--start-at")
            command.add_argument("--end-at")
            command.add_argument("--work-scope-id")
            if name in {"bindings", "outcomes"}:
                command.add_argument("--connector-id")
    view = commands.add_parser(
        "view",
        help="Read an authorized v1 finance, platform, or team aggregate view",
    )
    view.add_argument("view", choices=("finance", "platform", "team"))
    view.add_argument("resource", choices=("budgets", "scorecards"))
    view.add_argument(
        "--token-env",
        default="HORMUZ_PORTFOLIO_TOKEN",
        help="Environment variable holding an existing role-bound bearer token",
    )
    view.add_argument("--limit", type=int)
    view.add_argument("--cursor")
    view.add_argument("--start-at")
    view.add_argument("--end-at")
    view.add_argument("--work-scope-id")
    view.add_argument(
        "--format",
        choices=("terminal", "json", "csv"),
        default="terminal",
    )


def _spreadsheet_cell(value: object) -> str:
    """Keep every exported string inert in common spreadsheet programs."""

    rendered = "" if value is None else str(value)
    inspected = rendered.lstrip(" \t\r\n")
    if inspected.startswith(("=", "+", "-", "@")) or rendered.startswith(("\t", "\r", "\n")):
        return "'" + rendered
    return rendered


def _model_label(model: object) -> str:
    if type(model) is not dict:
        return "unknown"
    return "/".join(
        str(model.get(field) or "unknown")
        for field in ("provider_id", "model_id", "model_version")
    )


def _render_terminal(page: dict[str, object]) -> str:
    lines = [
        f"{page['view']} {page['resource']} view; scope={page['scope']['kind']}:"
        f"{page['scope']['id']}; as_of={page['as_of']}; results={page['result_count']}"
    ]
    for item in page["items"]:
        payload = item["payload"]
        scope = item["work_scope"]
        lines.append(
            f"{scope['work_scope_id']} v{scope['version']} | "
            f"{item['delivery_state']} | evidence={item['evidence_level']}"
        )
        if item["resource"] == "budget":
            enforcement = payload["enforcement"]
            forecast = payload["forecast"]
            lines.append(
                f"  plan={payload['plan']['id']} v{payload['plan']['version']} "
                f"amount={payload['currency']} {payload['plan_amount']} "
                f"committed={enforcement['committed_amount'] or 'unknown'} "
                f"pending={enforcement['pending_reservation_amount'] or 'unknown'} "
                f"uncertain={enforcement['uncertain_reservation_amount'] or 'unknown'} "
                f"remaining={enforcement['remaining_amount'] or 'unknown'}"
            )
            lines.append(
                f"  forecast={forecast['projected_amount'] or 'unknown'} "
                f"basis={forecast['cost_basis']} reason={forecast['reason_code']}"
            )
        else:
            models = ",".join(
                _model_label(model)
                for model in item["provenance"]["models"]
            ) or "unknown"
            lines.append(
                f"  scorecard={payload['scorecard_id']} v{payload['version']} "
                f"state={payload['state']} models={models}"
            )
            lines.append(
                f"  pareto={','.join(payload['pareto_cohort_ids']) or 'none'} "
                f"pricing_coverage={payload['coverage']['pricing']['ratio'] or 'unknown'}"
            )
    if page["next_cursor"] is not None:
        lines.append(f"next_cursor={page['next_cursor']}")
    return "\n".join(lines)


def _csv_rows(page: dict[str, object]) -> tuple[list[str], list[list[object]]]:
    fields = [
        "view", "resource", "organization_id", "team_id", "work_scope_id",
        "work_scope_version", "window_start_at", "window_end_at", "delivery_state",
        "evidence_level", "generated_at", "expires_at", "resource_id",
        "resource_version", "plan_amount", "currency", "committed_amount",
        "pending_reservation_amount", "uncertain_reservation_amount",
        "remaining_amount", "forecast_amount", "cost_basis", "models",
        "pareto_cohorts", "reason_codes", "page_as_of", "next_cursor",
    ]
    rows = []
    for item in page["items"]:
        payload = item["payload"]
        budget = item["resource"] == "budget"
        enforcement = payload["enforcement"] if budget else {}
        forecast = payload["forecast"] if budget else {}
        rows.append([
            item["view"], item["resource"], item["organization_id"], item["team_id"],
            item["work_scope"]["work_scope_id"], item["work_scope"]["version"],
            item["window"]["start_at"], item["window"]["end_at"], item["delivery_state"],
            item["evidence_level"], item["freshness"]["generated_at"],
            item["freshness"]["expires_at"],
            payload["plan"]["id"] if budget else payload["scorecard_id"],
            payload["plan"]["version"] if budget else payload["version"],
            payload["plan_amount"] if budget else None,
            payload["currency"] if budget else None,
            enforcement.get("committed_amount"), enforcement.get("pending_reservation_amount"),
            enforcement.get("uncertain_reservation_amount"), enforcement.get("remaining_amount"),
            forecast.get("projected_amount"),
            forecast.get("cost_basis") if budget else ";".join(item["provenance"]["cost_bases"]),
            ";".join(_model_label(model) for model in item["provenance"]["models"]),
            "" if budget else ";".join(payload["pareto_cohort_ids"]),
            ";".join(item["provenance"]["reason_codes"]),
            page["as_of"], page["next_cursor"],
        ])
    return fields, rows


def _print_view(page: dict[str, object], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(page, sort_keys=True, separators=(",", ":")))
        return
    if output_format == "terminal":
        print(_render_terminal(page))
        return
    fields, rows = _csv_rows(page)
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow([_spreadsheet_cell(field) for field in fields])
    for row in rows:
        writer.writerow([_spreadsheet_cell(value) for value in row])


def run(config, args) -> int:
    try:
        token = os.environ.get(args.token_env, "")
        authenticator = Authenticator(config)
        try:
            identity = authenticator.authenticate(token)
        except AuthenticationError:
            raise PortfolioError("unauthenticated") from None
        principal = authorize(config.portfolio_control, identity)
        name = args.portfolio_command
        if name == "view":
            required_role = {
                ("finance", "budgets"): "finance_viewer",
                ("platform", "scorecards"): "platform_viewer",
                ("team", "budgets"): "team_lead",
                ("team", "scorecards"): "team_lead",
            }.get((args.view, args.resource))
            if required_role is None:
                raise PortfolioError("invalid_request")
            if (
                required_role not in principal.roles
                or (required_role == "team_lead" and principal.team_id is None)
            ):
                raise PortfolioError("forbidden")
        elif "portfolio_admin" not in principal.roles:
            raise PortfolioError("forbidden")
        # Even local initialization/migration follows authorization. No caller
        # can use a denied portfolio command to open or create the database.
        repositories = create_repository_bundle(config, portfolio_factory=create_portfolio_repository)
        service = PortfolioService(config, repositories.portfolio, authenticator)
        if name == "view":
            path = ROLE_VIEWS + f"/{args.view}/{args.resource}"
            query = urlencode({
                key: getattr(args, key)
                for key in ("limit", "cursor", "start_at", "end_at", "work_scope_id")
                if getattr(args, key) is not None
            })
            _, result = service.dispatch_authorized(
                principal,
                "GET",
                path,
                query=query,
            )
            _print_view(result, args.format)
            return 0
        method = "GET" if name in {"list", "bindings", "show", "attributions", "outcomes"} else "POST"
        path = OUTCOMES if name == "outcomes" else ATTRIBUTIONS if name in {"attribute", "attributions"} else BINDINGS if name in {"bind", "bindings"} else SCOPES
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
