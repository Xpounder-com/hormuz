"""Two-provider HTTP fixtures; provider traffic and content stay synthetic."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from unittest import mock

from hormuz._portfolio_sql import PortfolioSQL
from hormuz._attribution_schema import TABLE_DDL
from hormuz.attribution_admission import RESULT_HEADER, select_admission
from hormuz.budget_runtime import RuntimeBudgetSQL
from hormuz.config import ModelRoute, UpstreamConfig
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import ATTRIBUTIONS, SCOPES, canonical
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.store import UsageStore

if __package__:
    from ._attribution_fixture import attributed_config, attribution_request
    from ._portfolio_fixture import ADMIN, VIEWER, create_request, registry_config, version_request
    from .test_gateway import FakeProviderHandler
else:
    from _attribution_fixture import attributed_config, attribution_request
    from _portfolio_fixture import ADMIN, VIEWER, create_request, registry_config, version_request
    from test_gateway import FakeProviderHandler


class NativeAttributionProvider(FakeProviderHandler):
    requests = []

    def _send_json(self, value, **kwargs):
        if isinstance(value, dict) and "model" in value:
            value = {**value, "model": "actual-anthropic-v1" if self.path.endswith("/messages") else "actual-openai-v1"}
        super()._send_json(value, **kwargs)

    def do_POST(self):
        if self.path != "/v1/responses/compact":
            return super().do_POST()
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        self._send_json({"id": "compact-synthetic", "object": "response.compaction", "output": [], "model": body["model"],
                         "usage": {"input_tokens": 1, "output_tokens": 1}}, request_id="synthetic-compact")


class AttributionGatewayAssertions:
    def setup_gateway(self, config, *, environment=None):
        self.environment = environment
        NativeAttributionProvider.requests = []
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), NativeAttributionProvider)
        self.provider_thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()
        self.addCleanup(self.close_provider)
        base = f"http://127.0.0.1:{self.provider.server_port}/v1"
        config = replace(config, upstreams={protocol: UpstreamConfig(base, api_key_env="SYNTHETIC_PROVIDER_KEY") for protocol in ("openai", "anthropic")},
                         model_routes={"synthetic": ModelRoute("synthetic", "openai", "routed-openai"),
                                       "synthetic-anthropic": ModelRoute("synthetic-anthropic", "anthropic", "routed-anthropic")},
                         upstream_timeout_seconds=5)
        if config.usage_storage.backend == "sqlite":
            UsageStore(config.database_path)
        service = PortfolioService(config, create_portfolio_repository(config, environ=environment))
        self.scope = service.dispatch(ADMIN, "POST", SCOPES, body=canonical(create_request()).encode(), idempotency_key="native-scope")[1]
        self.config = attributed_config(config, self.scope)
        bounded_environment = {
            **(environment or {}),
            "SYNTHETIC_PROVIDER_KEY": "synthetic-native-provider-key",
        }
        self.server = GatewayServer(self.config, environ=bounded_environment)
        self.thread = serve_in_thread(self.server)
        self.addCleanup(self.close_gateway)
        self.principal = self.server.portfolio_service.authenticate(ADMIN)
        self.header = f'v1;work_scope_id={self.scope["work_scope_id"]};version=1'

    def close_gateway(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def close_provider(self):
        self.provider.shutdown()
        self.provider.server_close()
        self.provider_thread.join(timeout=10)

    def restart_gateway(self, config):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        self.config = config
        self.server = GatewayServer(
            self.config,
            environ={
                **(self.environment or {}),
                "SYNTHETIC_PROVIDER_KEY": "synthetic-native-provider-key",
            },
        )
        self.thread = serve_in_thread(self.server)
        self.principal = self.server.portfolio_service.authenticate(ADMIN)

    def native(self, path="/v1/responses", *, headers=None, token=ADMIN,
               stream=False, include_output_limit=True, request_changes=None):
        anthropic = path.startswith("/v1/messages")
        output_field = "max_tokens" if anthropic else "max_output_tokens"
        body = {"model": "synthetic-anthropic" if anthropic else "synthetic",
                "messages" if anthropic else "input": [{"role": "user", "content": "SYNTHETIC_EXCLUDED_WORK_CONTENT"}],
                output_field: 20}
        if not include_output_limit:
            body.pop(output_field)
        if stream:
            body["stream"] = True
        if request_changes is not None:
            body.update(request_changes)
        payload = json.dumps(body).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        connection.putrequest("POST", path)
        connection.putheader("X-Api-Key" if anthropic else "Authorization", token if anthropic else "Bearer " + token)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(payload)))
        for value in ([self.header] if headers is None else headers):
            connection.putheader("X-Hormuz-Work-Scope", value)
        connection.endheaders(payload)
        response = connection.getresponse()
        status, response_headers, data = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, response_headers, data

    def events(self):
        return self.server.portfolio_service.dispatch(ADMIN, "GET", ATTRIBUTIONS)[1]["items"]

    def activate_scope_budget(self, *, amount="10"):
        now = datetime.now(timezone.utc)
        budget = self.server.budget_repository
        self.assertIsNotNone(budget)
        plan = budget.create_plan(self.principal, {
            "schema_id": "hormuz.work-budget-plan-request", "schema_version": 1,
            "budget_plan_id": None, "expected_version": None,
            "work_scope": {
                "work_scope_id": self.scope["work_scope_id"],
                "version": self.scope["version"],
            },
            "window": {
                "start_at": (now - timedelta(days=1)).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                "end_at": (now + timedelta(days=1)).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            },
            "currency": "USD", "amount": amount, "allowed_models": None,
            "output_token_cap": None, "per_request_cost_cap": None,
            "reason_code": "created",
        })
        budget.activate_plan(self.principal, plan["budget_plan_id"], {
            "schema_id": "hormuz.work-budget-plan-activation-request", "schema_version": 1,
            "version": 1, "expected_active_version": None,
            "expected_activation_generation": 0, "reason_code": "accepted",
        })
        return budget, plan

    def check_native_success_bodies_models_and_header_stripping(self):
        for path in ("/v1/responses", "/v1/messages", "/v1/responses/compact"):
            status, headers, data = self.native(path)
            self.assertEqual(status, 200)
            self.assertEqual(headers[RESULT_HEADER], "v1;status=attributed;reason=bound")
            self.assertNotIn("schema_id", json.loads(data))
            self.assertEqual(NativeAttributionProvider.requests[-1]["path"], path)
            self.assertNotIn("x-hormuz-work-scope", NativeAttributionProvider.requests[-1]["headers"])
        for path in ("/v1/responses", "/v1/messages"):
            status, headers, data = self.native(path, stream=True)
            self.assertEqual(status, 200)
            self.assertEqual(headers[RESULT_HEADER], "v1;status=attributed;reason=bound")
            self.assertIn(b"data:", data)
        events = self.events()
        self.assertEqual(len(events), 5)
        models = {self.server.attribution_repository.attempt_facts(self.principal, event["request_attempt_id"])["provider_reported_model"] for event in events}
        self.assertIn("actual-openai-v1", models)
        self.assertIn("actual-anthropic-v1", models)
        self.assertNotIn("SYNTHETIC_EXCLUDED_WORK_CONTENT", canonical(events))
        with self.server.attribution_repository._transaction("acme") as sql:
            for table in TABLE_DDL:
                rows = [dict(row) for row in sql.execute("SELECT * FROM " + table).fetchall()]
                self.assertNotIn("SYNTHETIC_EXCLUDED_WORK_CONTENT", canonical(rows))

    def check_rejection_precedes_budget_policy_and_provider(self):
        for path in ("/v1/responses", "/v1/messages", "/v1/responses/compact"):
            for values, reason in ((["SYNTHETIC_EXCLUDED_FILENAME/path"], "invalid_reference"),
                                   ([self.header, self.header], "ambiguous"),
                                   ([self.header.replace("version=1", "version=2")], "unauthorized_scope"),
                                   ([self.header.replace("v1;", "v2;")], "unsupported")):
                with mock.patch.object(self.server.policy_engine, "evaluate", side_effect=AssertionError("policy_before_authorization")):
                    with self.assertLogs("hormuz", level="DEBUG") as logs:
                        status, headers, data = self.native(path, headers=values)
                self.assertIn(status, (400, 403))
                self.assertEqual(headers[RESULT_HEADER], "v1;status=rejected;reason=" + reason)
                payload = json.loads(data)
                self.assertNotIn("schema_id", payload)
                self.assertIn("error", payload)
                if path.startswith("/v1/messages"):
                    self.assertEqual(payload["type"], "error")
                self.assertNotIn("SYNTHETIC_EXCLUDED", data.decode() + " ".join(logs.output))
        self.assertEqual(NativeAttributionProvider.requests, [])
        self.assertEqual(self.events(), [])
        self.assertEqual(self.server.store.active_budget_reservations(organization_id="acme"), 0)
        self.assertEqual(sum(row["receipts"] for row in self.server.attribution_repository.rejection_counts(self.principal)), 12)
        before = self.server.attribution_repository.rejection_counts(self.principal)
        insert = PortfolioSQL.insert
        def fail_receipt(sql, table, row):
            if table == "portfolio_attribution_rejections":
                error = self.psycopg.OperationalError if hasattr(self, "psycopg") else sqlite3.OperationalError
                raise error("SYNTHETIC_EXCLUDED_RECEIPT_FAILURE")
            return insert(sql, table, row)
        for path in ("/v1/responses", "/v1/messages"):
            with mock.patch.object(PortfolioSQL, "insert", fail_receipt):
                status, headers, data = self.native(path, headers=["invalid"])
            self.assertEqual(status, 503)
            self.assertEqual(headers[RESULT_HEADER], "v1;status=unavailable;reason=dependency_unavailable")
            self.assertNotIn("SYNTHETIC_EXCLUDED", data.decode())
        self.assertEqual(NativeAttributionProvider.requests, [])
        self.assertEqual(self.server.attribution_repository.rejection_counts(self.principal), before)

    def check_failed_atomic_attribution_commit_rolls_back_before_egress(self):
        insert = RuntimeBudgetSQL.insert
        def fail(sql, table, row):
            if table == "portfolio_attribution_events":
                error = self.psycopg.OperationalError if hasattr(self, "psycopg") else sqlite3.OperationalError
                raise error("SYNTHETIC_EXCLUDED_STORAGE_DETAIL")
            return insert(sql, table, row)
        for path in ("/v1/responses", "/v1/messages"):
            with mock.patch.object(RuntimeBudgetSQL, "insert", fail):
                status, headers, data = self.native(path)
            self.assertEqual(status, 503)
            self.assertEqual(headers[RESULT_HEADER], "v1;status=unavailable;reason=dependency_unavailable")
            self.assertNotIn("SYNTHETIC_EXCLUDED", data.decode())
        self.assertEqual(NativeAttributionProvider.requests, [])
        self.assertEqual(self.events(), [])
        self.assertEqual(self.server.store.active_budget_reservations(organization_id="acme"), 0)

    def check_unbounded_output_never_egresses_under_active_work_budget(self):
        self.activate_scope_budget()
        before = list(NativeAttributionProvider.requests)
        for path in ("/v1/responses", "/v1/messages"):
            status, response_headers, data = self.native(
                path, include_output_limit=False,
            )
            self.assertEqual(status, 403)
            self.assertEqual(
                response_headers["X-Hormuz-Error-Code"],
                "hormuz_budget_denied",
            )
            self.assertIn("bounded output-token estimate", data.decode())
        self.assertEqual(NativeAttributionProvider.requests, before)
        self.assertEqual(self.events(), [])

    def check_failover_creates_distinct_attribution_and_work_budget_evidence(self):
        primary = replace(
            self.config.model_routes["synthetic"],
            upstream_model="gpt-test-fast",
            input_cost_per_million=1,
            output_cost_per_million=2,
            failover_alias="synthetic-failover",
        )
        alternate = ModelRoute(
            "synthetic-failover",
            "openai",
            "gpt-test-deep",
            input_cost_per_million=2,
            output_cost_per_million=4,
        )
        self.restart_gateway(replace(
            self.config,
            model_routes={**self.config.model_routes, "synthetic": primary,
                          "synthetic-failover": alternate},
        ))

        now = datetime.now(timezone.utc)
        budget = self.server.budget_repository
        self.assertIsNotNone(budget)
        plan = budget.create_plan(self.principal, {
            "schema_id": "hormuz.work-budget-plan-request", "schema_version": 1,
            "budget_plan_id": None, "expected_version": None,
            "work_scope": {
                "work_scope_id": self.scope["work_scope_id"],
                "version": self.scope["version"],
            },
            "window": {
                "start_at": (now - timedelta(days=1)).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                "end_at": (now + timedelta(days=1)).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            },
            "currency": "USD", "amount": "10",
            "allowed_models": [
                {"provider_id": "openai", "model_id": "synthetic", "model_version": None},
                {"provider_id": "openai", "model_id": "synthetic-failover", "model_version": None},
            ],
            "output_token_cap": None, "per_request_cost_cap": None,
            "reason_code": "created",
        })
        budget.activate_plan(self.principal, plan["budget_plan_id"], {
            "schema_id": "hormuz.work-budget-plan-activation-request", "schema_version": 1,
            "version": 1, "expected_active_version": None,
            "expected_activation_generation": 0, "reason_code": "accepted",
        })

        status, headers, data = self.native(
            request_changes={"force_primary_rate_limit": True},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(headers["X-Hormuz-Failover"], "v1;reason=provider_rate_limited")
        self.assertEqual(
            [request["body"]["model"] for request in NativeAttributionProvider.requests],
            ["gpt-test-fast", "gpt-test-deep"],
        )

        events = self.events()
        self.assertEqual(len(events), 2)
        event_by_attempt = {
            event["request_attempt_id"]: event["attribution_event_id"]
            for event in events
        }
        with self.server.attribution_repository._transaction("acme") as sql:
            failover = dict(sql.execute(
                "SELECT original_attempt_id, failover_attempt_id, trigger_status, reason_code "
                "FROM gateway_provider_failover_events"
            ).fetchone())
            bindings = {
                row["request_attempt_id"]: dict(row)
                for row in sql.execute(
                    "SELECT request_attempt_id, attribution_event_id, provider_id, model_id "
                    "FROM portfolio_work_budget_reservation_bindings"
                ).fetchall()
            }
        attempt_ids = {failover["original_attempt_id"], failover["failover_attempt_id"]}
        self.assertEqual(set(event_by_attempt), attempt_ids)
        self.assertEqual(set(bindings), attempt_ids)
        for attempt_id in attempt_ids:
            self.assertEqual(bindings[attempt_id]["attribution_event_id"], event_by_attempt[attempt_id])
            self.assertEqual(bindings[attempt_id]["provider_id"], "openai")
        self.assertEqual(bindings[failover["original_attempt_id"]]["model_id"], "synthetic")
        self.assertEqual(
            bindings[failover["failover_attempt_id"]]["model_id"],
            "synthetic-failover",
        )
        self.assertEqual(failover["trigger_status"], 429)
        self.assertEqual(failover["reason_code"], "provider_rate_limited")

    def check_provider_side_input_never_egresses_under_active_work_budget(self):
        budget, plan = self.activate_scope_budget()
        before = list(NativeAttributionProvider.requests)
        remote_cases = (
            ("/v1/responses", {
                "input": [{
                    "role": "user",
                    "content": [{
                        "type": "input_image",
                        "image_url": "https://example.invalid/synthetic-image.png",
                    }],
                }],
            }),
            ("/v1/responses", {
                "input": [{"type": "input_file", "file_id": "file-synthetic"}],
            }),
            ("/v1/responses", {"previous_response_id": "resp-synthetic"}),
            ("/v1/responses", {"tools": [{"type": "web_search"}]}),
            ("/v1/messages", {
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": "https://example.invalid/synthetic-image.png",
                        },
                    }],
                }],
            }),
            ("/v1/messages", {
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "document",
                        "source": {"type": "file", "file_id": "file-synthetic"},
                    }],
                }],
            }),
            ("/v1/messages", {"container": "container-synthetic"}),
            ("/v1/messages", {
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
            }),
        )
        for path, changes in remote_cases:
            with self.subTest(path=path, changes=changes):
                status, response_headers, data = self.native(
                    path, request_changes=changes,
                )
                self.assertEqual(status, 403)
                self.assertEqual(
                    response_headers["X-Hormuz-Error-Code"],
                    "hormuz_budget_denied",
                )
                self.assertIn("bounded input-token estimate", data.decode())
        self.assertEqual(NativeAttributionProvider.requests, before)
        self.assertEqual(self.events(), [])
        self.assertEqual(
            self.server.store.active_budget_reservations(organization_id="acme"),
            0,
        )
        report = budget.current_report(self.principal, plan["budget_plan_id"])
        self.assertEqual(report["enforcement"]["over_cap_attempts"], len(remote_cases))
        self.assertEqual(report["coverage"]["population_attempts"], len(remote_cases))
        self.assertEqual(report["coverage"]["included_attempts"], len(remote_cases))

        inline_cases = (
            ("/v1/responses", {
                "input": [{
                    "role": "user",
                    "content": [{
                        "type": "input_image",
                        "image_url": "data:image/png;base64,iVBORw0KGgo=",
                    }],
                }],
            }),
            ("/v1/responses", {
                "input": [{
                    "type": "input_file",
                    "filename": "synthetic.txt",
                    "file_data": "U1lOVEhFVElDX0lOTElORQ==",
                }],
            }),
            ("/v1/messages", {
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        },
                    }],
                }],
            }),
        )
        for path, changes in inline_cases:
            with self.subTest(path=path, inline=True):
                self.assertEqual(
                    self.native(path, request_changes=changes)[0],
                    200,
                )
        self.assertEqual(len(NativeAttributionProvider.requests), len(before) + len(inline_cases))
        self.assertEqual(len(self.events()), len(inline_cases))

    def check_postgres_unbound_identity_uses_database_clock(self):
        budget, plan = self.activate_scope_budget()
        before = list(NativeAttributionProvider.requests)

        class AheadProcessClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(timezone.utc) + timedelta(days=2)

        with mock.patch("hormuz.postgres_usage_store.datetime", AheadProcessClock):
            status, response_headers, data = self.native(
                token=VIEWER, headers=[],
            )

        self.assertEqual(status, 403)
        self.assertEqual(
            response_headers["X-Hormuz-Error-Code"],
            "hormuz_budget_denied",
        )
        self.assertIn("attribution is required", data.decode())
        self.assertEqual(NativeAttributionProvider.requests, before)
        self.assertEqual(self.events(), [])
        self.assertEqual(
            self.server.store.active_budget_reservations(organization_id="acme"),
            0,
        )
        report = budget.current_report(self.principal, plan["budget_plan_id"])
        self.assertEqual(report["coverage"]["population_attempts"], 1)
        self.assertEqual(report["coverage"]["unattributed_attempts"], 1)

    def check_unbound_identity_cannot_bypass_an_active_work_budget(self):
        budget, plan = self.activate_scope_budget()
        before = list(NativeAttributionProvider.requests)
        unbound = self.config.identities_by_token[VIEWER]
        self.assertIsNone(
            select_admission(
                self.config, unbound, "codex", [], account_usage=True,
            )
        )

        status, response_headers, data = self.native(
            token=VIEWER, headers=[],
        )

        self.assertEqual(status, 403)
        self.assertEqual(
            response_headers["X-Hormuz-Error-Code"],
            "hormuz_budget_denied",
        )
        self.assertIn("attribution is required", data.decode())
        self.assertEqual(NativeAttributionProvider.requests, before)
        self.assertEqual(self.events(), [])
        self.assertEqual(
            self.server.store.active_budget_reservations(organization_id="acme"),
            0,
        )
        report = budget.current_report(self.principal, plan["budget_plan_id"])
        self.assertEqual(report["coverage"]["population_attempts"], 1)
        self.assertEqual(report["coverage"]["unattributed_attempts"], 1)

    def check_scope_change_before_atomic_reservation_never_egresses(self):
        begin = self.server.policy_engine.begin_request_attempt
        def advance(**kwargs):
            self.server.portfolio_service.dispatch(ADMIN, "POST", SCOPES + "/" + self.scope["work_scope_id"] + "/versions",
                                                  body=canonical(version_request(self.scope)).encode(), idempotency_key="native-advance")
            return begin(**kwargs)
        with mock.patch.object(self.server.policy_engine, "begin_request_attempt", side_effect=advance):
            status, headers, _ = self.native()
        self.assertEqual(status, 409)
        self.assertEqual(headers[RESULT_HEADER], "v1;status=rejected;reason=stale_version")
        self.assertEqual(NativeAttributionProvider.requests, [])
        self.assertEqual(self.events(), [])
        self.assertEqual(self.server.store.active_budget_reservations(organization_id="acme"), 0)
        totals = self.server.store.monthly_totals(organization_id="acme")
        self.assertEqual(totals.cost_usd, 0)

    def check_unattributed_default_and_nonaccounted_behavior(self):
        status, headers, _ = self.native(headers=[])
        self.assertEqual(status, 200)
        self.assertEqual(headers[RESULT_HEADER], "v1;status=unattributed;reason=missing_evidence")
        self.assertEqual(self.events()[0]["confidence"], "unattributed")
        count = len(self.events())
        self.assertEqual(self.native("/v1/messages/count_tokens")[0], 400)
        status, headers, data = self.native("/v1/messages/count_tokens", headers=[])
        self.assertEqual(status, 200)
        self.assertNotIn(RESULT_HEADER, headers)
        self.assertEqual(json.loads(data), {"input_tokens": 42})
        self.assertEqual(len(self.events()), count)

    def check_unauthenticated_or_unbound_identity_cannot_lookup(self):
        with mock.patch.object(self.server.attribution_repository, "preflight", side_effect=AssertionError("unauthenticated_lookup")):
            status, headers, _ = self.native(token="unknown")
        self.assertEqual(status, 401)
        self.assertNotIn(RESULT_HEADER, headers)
        status, headers, _ = self.native(token=VIEWER)
        self.assertEqual(status, 403)
        self.assertEqual(headers[RESULT_HEADER], "v1;status=rejected;reason=unauthorized_scope")
        self.assertEqual(NativeAttributionProvider.requests, [])

    def check_admin_http_matches_versioned_correction_contract(self):
        self.assertEqual(self.native()[0], 200)
        initial = self.events()[0]
        body = canonical(attribution_request(initial["request_attempt_id"], self.scope, initial["attribution_event_id"])).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        connection.request("POST", ATTRIBUTIONS, body=body, headers={"Authorization": "Bearer " + ADMIN, "Content-Type": "application/json", "Idempotency-Key": "native-correct"})
        response = connection.getresponse()
        self.assertEqual(response.status, 201)
        self.assertEqual(response.getheader("X-Hormuz-Contract"), "hormuz.governed-run-attribution-event;v=1")
        self.assertNotIn(RESULT_HEADER, dict(response.getheaders()))
        corrected = json.loads(response.read())
        connection.close()
        self.assertEqual(corrected["supersedes_event_id"], initial["attribution_event_id"])
