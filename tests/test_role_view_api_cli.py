from __future__ import annotations

import contextlib
import csv
from dataclasses import asdict, replace
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz.cli import main
from hormuz.commands.portfolio import _csv_rows, _print_view, _spreadsheet_cell
from hormuz.config import ConfigError
from hormuz.portfolio_config import authorize, build_portfolio_config
from hormuz.portfolio_wire import PortfolioError, ROLE_VIEWS
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.store import UsageStore

from ._role_view_fixture import (
    FINANCE_TOKEN,
    PLATFORM_TOKEN,
    TEAM_TOKEN,
    role_view_config,
)


def configuration_document(config):
    identities = []
    environment = {"SYNTHETIC_PROVIDER_KEY": "synthetic-provider-test-key"}
    for index, identity in enumerate(config.identities_by_token.values()):
        name = f"SYNTHETIC_ROLE_VIEW_TOKEN_{index}"
        environment[name] = identity.token
        identities.append({
            "token_env": name,
            "actor_id": identity.actor_id,
            "actor_name": identity.actor_name,
            "team_id": identity.team_id,
            "team_name": identity.team_name,
            "organization_id": identity.organization_id,
            "allowed_clients": list(identity.allowed_clients),
        })
    portfolio = json.loads(json.dumps(asdict(config.portfolio_control)))
    portfolio.update({
        "schema_id": "hormuz.portfolio-control",
        "schema_version": 1,
    })
    return {
        "database": str(config.database_path),
        "identities": identities,
        "upstreams": {
            protocol: {
                "base_url": "http://127.0.0.1:1/v1",
                "api_key_env": "SYNTHETIC_PROVIDER_KEY",
            }
            for protocol in ("openai", "anthropic")
        },
        "model_routes": {
            "synthetic": {
                "protocol": "openai",
                "upstream_model": "synthetic",
            }
        },
        "policies": {"organization": {}},
        "portfolio_control": portfolio,
    }, environment


class PortfolioRoleViewAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = role_view_config(Path(temporary.name))
        self.server = GatewayServer(
            self.config,
            environ={"SYNTHETIC_PROVIDER_KEY": "synthetic-provider-test-key"},
        )
        self.thread = serve_in_thread(self.server)
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, token):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.putrequest("GET", path)
            connection.putheader("Authorization", "Bearer " + token)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def test_versioned_http_routes_enforce_exact_roles_without_provider_egress(self):
        cases = (
            (FINANCE_TOKEN, "/finance/budgets", "finance_viewer"),
            (PLATFORM_TOKEN, "/platform/scorecards", "platform_viewer"),
            (TEAM_TOKEN, "/team/budgets", "team_lead"),
            (TEAM_TOKEN, "/team/scorecards", "team_lead"),
        )
        with mock.patch(
            "hormuz.server._open_upstream",
            side_effect=AssertionError("role view attempted provider egress"),
        ):
            for token, suffix, role in cases:
                status, page, headers = self.request(ROLE_VIEWS + suffix, token)
                self.assertEqual(status, 200)
                self.assertEqual(page["reader_role"], role)
                self.assertEqual(page["items"], [])
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(
                    headers["X-Hormuz-Contract"],
                    "hormuz.portfolio-role-view-page;v=1",
                )
        self.assertEqual(
            self.request(ROLE_VIEWS + "/platform/scorecards", FINANCE_TOKEN)[0],
            403,
        )
        self.assertEqual(
            self.request(ROLE_VIEWS + "/finance/budgets?limit=101", FINANCE_TOKEN)[0],
            400,
        )

    def test_team_lead_requires_a_bounded_opaque_team_id(self):
        document, _ = configuration_document(self.config)
        identities = tuple(self.config.identities_by_token.values())
        team_identity = self.config.identities_by_token[TEAM_TOKEN]
        for team_id in (None, "bad team", "x" * 129):
            invalid = replace(team_identity, team_id=team_id)
            changed = tuple(
                invalid if identity is team_identity else identity
                for identity in identities
            )
            with self.subTest(team_id=team_id):
                with self.assertRaisesRegex(
                    ConfigError,
                    "^portfolio_configuration_invalid$",
                ):
                    build_portfolio_config(
                        document["portfolio_control"],
                        changed,
                    )
                with self.assertRaisesRegex(PortfolioError, "^forbidden$"):
                    authorize(self.config.portfolio_control, invalid)


class PortfolioRoleViewCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = role_view_config(self.root)
        UsageStore(self.config.database_path)
        document, environment = configuration_document(self.config)
        self.config.source_path.write_text(json.dumps(document), encoding="utf-8")
        self.environment = environment
        self.finance_env = next(
            name for name, value in environment.items() if value == FINANCE_TOKEN
        )

    def command(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = main([
                    "--config", str(self.config.source_path),
                    "portfolio", *arguments,
                ])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cli_json_terminal_and_csv_share_the_versioned_route(self):
        common = (
            "view", "finance", "budgets",
            "--token-env", self.finance_env,
        )
        status, output, error = self.command(*common, "--format", "json")
        self.assertEqual((status, error), (0, ""))
        page = json.loads(output)
        self.assertEqual(page["schema_id"], "hormuz.portfolio-role-view-page")

        status, output, error = self.command(*common, "--format", "terminal")
        self.assertEqual((status, error), (0, ""))
        self.assertIn("finance budget view", output)
        self.assertIn("results=0", output)

        status, output, error = self.command(*common, "--format", "csv")
        self.assertEqual((status, error), (0, ""))
        rows = list(csv.reader(io.StringIO(output)))
        self.assertEqual(len(rows), 1)
        self.assertIn("work_scope_id", rows[0])

        status, _, error = self.command(
            "view", "finance", "scorecards",
            "--token-env", self.finance_env,
        )
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(error)["code"], "invalid_request")

    def test_csv_cells_are_formula_safe_and_standardly_quoted(self):
        for value in ("=SUM(A1:A2)", " +1", "-2", "@name", "\t=cmd"):
            self.assertTrue(_spreadsheet_cell(value).startswith("'"))
        self.assertEqual(_spreadsheet_cell("safe,value"), "safe,value")
        page = {
            "view": "finance",
            "resource": "budget",
            "organization_id": "acme",
            "scope": {"kind": "organization", "id": "acme"},
            "as_of": "2026-09-24T00:00:00.000000Z",
            "result_count": 0,
            "items": [],
            "next_cursor": None,
        }
        fields, rows = _csv_rows(page)
        self.assertTrue(fields)
        self.assertEqual(rows, [])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            _print_view(page, "csv")
        self.assertEqual(len(list(csv.reader(io.StringIO(output.getvalue())))), 1)


if __name__ == "__main__":
    unittest.main()
