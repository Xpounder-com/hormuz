"""Named synthetic registry fixtures shared by live SQLite/PostgreSQL tests."""

from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from hormuz.config import GatewayConfig, Identity, ListenConfig, ModelRoute, Policy, UpstreamConfig
from hormuz.portfolio_config import PortfolioConfig, PortfolioConnectorBinding, PortfolioRoleBinding
from hormuz.portfolio_repository import RegistryRepository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import BINDINGS, SCOPES, PortfolioError, validate


ADMIN = "synthetic-registry-admin-token"
OTHER = "synthetic-registry-other-token"
VIEWER = "synthetic-registry-viewer-token"
SELF = "synthetic-registry-self-token"


def registry_config(root: Path) -> GatewayConfig:
    identities = {
        token: Identity(token_env="UNUSED_REGISTRY_TEST_TOKEN", token=token, actor_id=actor,
                        actor_name="Synthetic", team_id="engineering", team_name="Synthetic",
                        organization_id=organization, allowed_clients=())
        for token, actor, organization in ((ADMIN, "alice", "acme"), (OTHER, "bob", "beta"),
                                          (VIEWER, "finance", "acme"), (SELF, "self", "acme"))
    }
    return GatewayConfig(
        source_path=root / "config.json", listen=ListenConfig(port=0), database_path=root / "usage.sqlite3",
        upstreams={protocol: UpstreamConfig("http://127.0.0.1:1/v1", api_key_env="SYNTHETIC_PROVIDER_KEY")
                   for protocol in ("openai", "anthropic")}, identities_by_token=identities,
        model_routes={"synthetic": ModelRoute("synthetic", "openai", "synthetic")}, organization_policy=Policy(),
        portfolio_control=PortfolioConfig(
            (PortfolioRoleBinding("acme", "alice", ("portfolio_admin",)),
             PortfolioRoleBinding("beta", "bob", ("portfolio_admin",)),
             PortfolioRoleBinding("acme", "finance", ("finance_viewer",))),
            (PortfolioConnectorBinding("acme", "github-one", "github", "123", None, ("456", "789")),
             PortfolioConnectorBinding("beta", "github-other", "github", "987", None, ("654",))),
        ),
    )


def create_request(**changes):
    return {"schema_id": "hormuz.work-scope-create-request", "schema_version": 1, "kind": "use_case",
            "parent_work_scope_id": None, "owner_team_id": "engineering", "display_name": "Synthetic use case", **changes}


def version_request(scope, **changes):
    return {"schema_id": "hormuz.work-scope-version-request", "schema_version": 1,
            "expected_version": scope["version"],
            "parent_work_scope_id": scope["parent"]["work_scope_id"] if scope["parent"] else None,
            "owner_team_id": scope["owner_team_id"], "display_name": scope["display_name"],
            "state": scope["state"], "reason_code": "corrected", **changes}


def binding_request(scope, **changes):
    return {"schema_id": "hormuz.external-work-binding-request", "schema_version": 1,
            "connector_id": "github-one", "external_object_id": "456",
            "work_scope": {"work_scope_id": scope["work_scope_id"], "version": scope["version"]},
            "expected_binding_event_id": None, "state": "active", "reason_code": "bound", **changes}


def seed_registry_metadata(config, *, environ=None):
    """Populate all five registry tables for real backup/restore proofs."""
    service = PortfolioService(config, RegistryRepository(config, environ=environ))
    writes = []

    def write(path, body, key):
        result = service.dispatch(ADMIN, "POST", path, body=json.dumps(body).encode(), idempotency_key=key)
        writes.append((path, body, key, result))
        return result[1]

    parent = write(SCOPES, create_request(kind="portfolio"), "recovery-parent")
    child = write(SCOPES, create_request(parent_work_scope_id=parent["work_scope_id"]), "recovery-child")
    current = write(SCOPES + "/" + child["work_scope_id"] + "/versions",
                    version_request(child, display_name="Synthetic corrected label"), "recovery-version")
    write(BINDINGS, binding_request(current), "recovery-binding")
    page = service.dispatch(ADMIN, "GET", SCOPES, query="limit=1")[1]
    return writes, page


class RegistryAssertions:
    def call(self, method="GET", path=SCOPES, *, token=ADMIN, value=None, key=None, query=""):
        status, result = self.service.dispatch(token, method, path, body=json.dumps(value).encode() if value is not None else b"",
                                               idempotency_key=key, query=query)
        self.assertEqual(status, 201 if method == "POST" else 200)
        validate(result, result["schema_id"])
        return result

    def create(self, key="create", **changes):
        return self.call("POST", value=create_request(**changes), key=key)

    def version(self, scope, key="version", **changes):
        return self.call("POST", SCOPES + "/" + scope["work_scope_id"] + "/versions",
                         value=version_request(scope, **changes), key=key)

    def raises_code(self, code, function):
        with self.assertRaises(PortfolioError) as caught:
            function()
        self.assertEqual(caught.exception.code, code)

    def check_lifecycle_and_hierarchy(self):
        portfolio = self.create("portfolio", kind="portfolio")
        initiative = self.create("initiative", kind="initiative", parent_work_scope_id=portfolio["work_scope_id"])
        use_case = self.create("use-case", parent_work_scope_id=initiative["work_scope_id"])
        self.assertEqual(use_case["parent"], {"work_scope_id": initiative["work_scope_id"], "version": 1})
        self.raises_code("invalid_request", lambda: self.version(portfolio, parent_work_scope_id=use_case["work_scope_id"], reason_code="reparented"))
        initiative2 = self.version(initiative, "initiative-two", display_name="Changed synthetic label")
        self.assertEqual(self.call(path=SCOPES + "/" + use_case["work_scope_id"])["parent"]["version"], 1)
        self.assertEqual(initiative2["version"], 2)
        archived = self.version(use_case, "archive", state="archived", reason_code="archived")
        active = self.version(archived, "reactivate", state="active", reason_code="reactivated")
        tombstone = self.version(active, "tombstone", state="tombstoned", display_name=None, reason_code="tombstoned")
        self.assertIsNone(tombstone["display_name"])
        self.assertEqual(self.call(path=SCOPES + "/" + use_case["work_scope_id"], query="version=1"), use_case)
        self.raises_code("version_conflict", lambda: self.version(tombstone, "resurrect", state="active", display_name="No", reason_code="reactivated"))
        self.assertEqual(len(self.call()["items"]), 3)

    def check_authorization_before_access(self):
        with mock.patch.object(self.repository, "_transaction", side_effect=AssertionError("unauthorized storage access")):
            for token, code in (("invalid", "unauthenticated"), (VIEWER, "forbidden"), (SELF, "forbidden")):
                self.raises_code(code, lambda token=token: self.call(token=token))
        scope = self.create()
        self.assertEqual(self.call(token=OTHER)["items"], [])
        self.raises_code("not_found", lambda: self.call(path=SCOPES + "/" + scope["work_scope_id"], token=OTHER))
        self.raises_code("not_found", lambda: self.call("POST", value=create_request(parent_work_scope_id=scope["work_scope_id"]), key="foreign", token=OTHER))
        with mock.patch.object(self.repository, "_transaction", side_effect=AssertionError("unauthorized binding lookup")):
            self.raises_code("forbidden", lambda: self.call("POST", BINDINGS, value=binding_request(scope, connector_id="github-other"), key="foreign"))
            self.raises_code("forbidden", lambda: self.call("POST", BINDINGS, value=binding_request(scope, external_object_id="999"), key="unbound"))

    def check_idempotency_and_versions(self):
        first = self.create()
        before = self.registry_rows()
        self.assertEqual(self.create(), first)
        self.assertEqual(self.registry_rows(), before)
        self.raises_code("idempotency_conflict", lambda: self.create(display_name="Changed"))
        second = self.version(first)
        self.assertEqual(self.version(first), second)
        self.raises_code("version_conflict", lambda: self.version(first, "stale"))
        self.assertEqual(self.call(path=SCOPES + "/" + first["work_scope_id"], query="version=1"), first)
        for key in (None, "", "a/b", "x" * 129):
            self.raises_code("invalid_request", lambda key=key: self.call("POST", value=create_request(), key=key))

    def check_frozen_pagination(self):
        from hormuz.portfolio_repository import _SQL
        with mock.patch.object(_SQL, "now", return_value="2026-01-01T00:00:00.000000Z"):
            first = self.create("first")
            second = self.create("second")
            third = self.create("third")
        page = self.call(query="limit=1")
        self.assertTrue(page["has_more"])
        self.create("later")
        updated = self.version(first, "changed", display_name="Later version")
        seen = page["items"][:]
        cursor = page["next_cursor"]
        while cursor:
            continuation = self.call(query="cursor=" + cursor + "&limit=1")
            self.assertEqual(continuation["as_of"], page["as_of"])
            seen.extend(continuation["items"])
            cursor = continuation["next_cursor"]
        self.assertEqual({item["work_scope_id"] for item in seen}, {first["work_scope_id"], second["work_scope_id"], third["work_scope_id"]})
        self.assertEqual(len(seen), 3)
        self.assertEqual([item["work_scope_id"] for item in seen], sorted((first["work_scope_id"], second["work_scope_id"], third["work_scope_id"]), reverse=True))
        self.assertTrue(all(item["version"] == 1 for item in seen))
        self.assertEqual(self.call(path=SCOPES + "/" + first["work_scope_id"]), updated)
        for query in ("cursor=" + page["next_cursor"] + "&work_scope_id=" + first["work_scope_id"], "cursor=forged"):
            self.raises_code("cursor_invalid", lambda query=query: self.call(query=query))
        self.raises_code("cursor_invalid", lambda: self.call(query="cursor=" + page["next_cursor"], token=OTHER))
        self.raises_code("cursor_invalid", lambda: self.call(path=BINDINGS, query="cursor=" + page["next_cursor"]))
        authority = self.config.portfolio_control
        for token, roles in (
            (SELF, (*authority.role_bindings, PortfolioRoleBinding("acme", "self", ("portfolio_admin",)))),
            (ADMIN, (replace(authority.role_bindings[0], roles=("platform_viewer", "portfolio_admin")), *authority.role_bindings[1:])),
        ):
            config = replace(self.config, portfolio_control=replace(authority, role_bindings=roles))
            service = PortfolioService(config, RegistryRepository(config, environ=self.registry_environment))
            self.raises_code("cursor_invalid", lambda: service.dispatch(token, "GET", SCOPES, query="cursor=" + page["next_cursor"]))
        expired = (datetime.fromisoformat(page["as_of"]) + timedelta(hours=1, seconds=1)).isoformat().replace("+00:00", "Z")
        with mock.patch.object(_SQL, "now", return_value=expired):
            self.raises_code("cursor_invalid", lambda: self.call(query="cursor=" + page["next_cursor"]))
        selected = self.call(query="start_at=2026-01-01T00:00:00Z&end_at=2026-01-02T00:00:00Z")["items"]
        self.assertEqual({item["work_scope_id"] for item in selected}, {second["work_scope_id"], third["work_scope_id"]})
        for index in range(50):
            self.create("page-size-" + str(index))
        self.assertEqual(len(self.call()["items"]), 50)
        self.assertEqual(len(self.call(query="limit=100")["items"]), 54)

    def check_bindings(self):
        scope = self.create()
        first = self.call("POST", BINDINGS, value=binding_request(scope), key="bind")
        self.assertEqual(self.call("POST", BINDINGS, value=binding_request(scope), key="bind"), first)
        self.raises_code("version_conflict", lambda: self.call("POST", BINDINGS, value=binding_request(scope), key="stale"))
        second = self.call("POST", BINDINGS, value=binding_request(scope, expected_binding_event_id=first["binding_event_id"], reason_code="corrected"), key="correct")
        tombstone = self.call("POST", BINDINGS, value=binding_request(scope, expected_binding_event_id=second["binding_event_id"], state="tombstoned", reason_code="tombstoned"), key="tombstone")
        self.assertEqual(tombstone["supersedes_event_id"], second["binding_event_id"])
        events = self.call(path=BINDINGS, query="work_scope_id=" + scope["work_scope_id"])["items"]
        self.assertEqual(len(events), 3)
        self.assertIn(first, events)
        updated = self.version(scope)
        self.raises_code("version_conflict", lambda: self.call("POST", BINDINGS, value=binding_request(scope, external_object_id="789"), key="old-version"))
        self.call("POST", BINDINGS, value=binding_request(updated, external_object_id="789"), key="new-version")

    def check_concurrent_writers(self):
        barrier = threading.Barrier(6)
        def create():
            service = PortfolioService(self.config, RegistryRepository(self.config, environ=self.registry_environment))
            barrier.wait(timeout=10)
            return service.dispatch(ADMIN, "POST", SCOPES, body=json.dumps(create_request()).encode(), idempotency_key="concurrent")[1]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: create(), range(6)))
        self.assertTrue(all(value == results[0] for value in results))
        self.assertEqual(len(self.call()["items"]), 1)
        scope = results[0]
        barrier = threading.Barrier(2)
        def version(key):
            service = PortfolioService(self.config, RegistryRepository(self.config, environ=self.registry_environment))
            barrier.wait(timeout=10)
            try:
                return service.dispatch(ADMIN, "POST", SCOPES + "/" + scope["work_scope_id"] + "/versions",
                                        body=json.dumps(version_request(scope)).encode(), idempotency_key=key)[0]
            except PortfolioError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(version, ("one", "two")))
        self.assertCountEqual(results, [201, "version_conflict"])

    def check_atomic_failure_and_safe_audit(self):
        from hormuz.portfolio_repository import _SQL
        original = _SQL.insert
        def fail(sql, table, row):
            if table == "portfolio_idempotency":
                raise RuntimeError("synthetic failure before commit")
            original(sql, table, row)
        before = self.registry_rows()
        with mock.patch.object(_SQL, "insert", fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                self.create()
        self.assertEqual(self.registry_rows(), before)
        created = self.create(display_name="SYNTHETIC_LABEL_NOT_FOR_AUDIT")
        self.call()
        self.call(path=SCOPES + "/" + created["work_scope_id"])
        rows = self.registry_rows()
        audit = rows["portfolio_audit_events"]
        self.assertNotIn("SYNTHETIC_LABEL_NOT_FOR_AUDIT", json.dumps(audit))
        self.assertEqual(len(audit), 3)
        self.assertEqual(sorted(row["sequence"] for row in audit), [1, 2, 3])
        self.create("second")
        before = self.registry_rows()
        def fail_audit(sql, table, row):
            if table == "portfolio_audit_events":
                raise RuntimeError("synthetic read audit failure")
            original(sql, table, row)
        with mock.patch.object(_SQL, "insert", fail_audit):
            with self.assertRaisesRegex(RuntimeError, "synthetic read audit failure"):
                self.call(query="limit=1")
        self.assertEqual(self.registry_rows(), before)  # The staged cursor also rolls back.
        with mock.patch.object(self.repository, "_scope_response", return_value={"schema_id": "hormuz.work-scope-version"}):
            self.raises_code("unavailable", lambda: self.call(path=SCOPES + "/" + created["work_scope_id"]))
        self.assertEqual(self.registry_rows(), before)

    def check_strict_input(self):
        for value in (create_request(organization_id="beta"), create_request(actor_id="bob"),
                      create_request(display_name="x" * 121), create_request(kind="employee"),
                      create_request(owner_team_id="unknown"), create_request(schema_version=True),
                      create_request(display_name="bad\nname"), create_request(title="excluded")):
            self.raises_code("invalid_request", lambda value=value: self.call("POST", value=value, key="bad"))
        for raw in (b'{"a":1,"a":2}', b'\xef\xbb\xbf{}', b'{} trailing', b'{"n":NaN}', b'{"n":1e999}', b'[]', b'{"x":"\\ud800"}'):
            self.raises_code("invalid_request", lambda raw=raw: self.service.dispatch(ADMIN, "POST", SCOPES, body=raw, idempotency_key="bad"))
        for query in ("limit=01", "limit=-1", "limit=101", "limit=1&limit=2", "organization_id=beta", "start_at=2026-01-01T00:00:00Z",
                      "start_at=2026-01-02T00:00:00Z&end_at=2026-01-01T00:00:00Z", "title=SYNTHETIC_EXCLUDED", "limit=%xx"):
            self.raises_code("invalid_request", lambda query=query: self.call(query=query))
        self.assertEqual(self.registry_rows()["portfolio_audit_events"], [])
        principal = self.service.authenticate(ADMIN)
        with mock.patch.object(self.repository, "_transaction", side_effect=AssertionError("invalid internal request reached storage")):
            for query in ({"limit": 1000000}, {"version": 1}, {"organization_id": "beta"}):
                self.raises_code("invalid_request", lambda query=query: self.repository.execute(
                    principal, "list_scopes", path=SCOPES, scope_id=None, query=query, body=None, idempotency_key=None))
