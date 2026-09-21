"""Synthetic #220 preflight oracle; no Linear service, credentials, or receiver."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.verify_linear_connector_preflight import (
    Authority, PreparedRegistry, PreflightError, SigningKey, authenticate_candidate, validate_registry,
    verify_plan,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_790_000_000_000
WORKSPACE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
WEBHOOK = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TEAM = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OTHER_TEAM = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
DELIVERY = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
OTHER_DELIVERY = "ffffffff-ffff-4fff-8fff-ffffffffffff"
OBJECTS = {
    "initiative": ("11111111-1111-4111-8111-111111111111",),
    "project": ("22222222-2222-4222-8222-222222222222",),
    "cycle": ("33333333-3333-4333-8333-333333333333",),
    "issue": ("44444444-4444-4444-8444-444444444444",),
}
SECRET_ACTIVE = b"a" * 32
SECRET_PREVIOUS = b"b" * 32
FINGERPRINT_KEY = b"f" * 32


def authority(**changes):
    original = Authority(
        route_id="synthetic-linear-route", organization_id="tenant-test",
        connector_id="linear-test", workspace_id=WORKSPACE, webhook_id=WEBHOOK,
        team_ids=(TEAM,), typed_object_ids=OBJECTS,
        signing_keys=(SigningKey("active-v2", SECRET_ACTIVE),
                      SigningKey("previous-v1", SECRET_PREVIOUS, NOW + 1000)),
    )
    return replace(original, **changes)


def signed_request(*, kind="Issue", action="create", data=None, body_changes=None,
                   header_changes=None, secret=SECRET_ACTIVE, delivery=DELIVERY):
    object_id = OBJECTS[kind.lower()][0] if kind.lower() in OBJECTS else OBJECTS["issue"][0]
    body = {
        "organizationId": WORKSPACE, "webhookId": WEBHOOK,
        "webhookTimestamp": NOW, "type": kind, "action": action,
        "data": data if data is not None else {"id": object_id, "teamId": TEAM},
    }
    body.update(body_changes or {})
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    headers = {
        "Linear-Signature": hmac.new(secret, raw, hashlib.sha256).hexdigest(),
        "Linear-Delivery": delivery, "Linear-Event": kind,
    }
    headers.update(header_changes or {})
    return list(headers.items()), raw


class LinearConnectorPreflightTests(unittest.TestCase):
    def setUp(self):
        self.registry = validate_registry({"synthetic-linear-route": authority()})

    def verify(self, headers, raw, *, registry=None, route="synthetic-linear-route", now=NOW):
        selected_registry = self.registry if registry is None else (
            registry if isinstance(registry, PreparedRegistry) else validate_registry(registry)
        )
        return authenticate_candidate(
            route_id=route, registry=selected_registry,
            headers=headers, raw=raw, now_ms=now, fingerprint_key=FINGERPRINT_KEY,
            fingerprint_key_version="fingerprint-test-v1",
        )

    def denied(self, code, headers, raw, **kwargs):
        with self.assertRaises(PreflightError) as caught:
            self.verify(headers, raw, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_static_plan_pins_frozen_contracts_and_keeps_all_gates_false(self):
        self.assertEqual(verify_plan()["status"], "offline_linear_preflight_plan_verified")
        plan = json.loads((ROOT / "docs/linear-connector-preflight-v1.json").read_text())
        self.assertEqual(set(plan["normalization"]["generic_authenticated_envelope_allowlist"]),
                         {"Issue", "Project", "Initiative", "Cycle"})
        self.assertIsNone(plan["successor_schema"]["linear_sqlite"])
        self.assertIsNone(plan["successor_schema"]["linear_postgresql"])
        self.assertFalse(any(plan["gates"].values()))
        self.assertEqual(plan["acknowledgment"]["maximum_internal_elapsed_ms"], 4000)
        self.assertEqual(plan["acknowledgment"]["linear_external_deadline_ms"], 5000)
        with tempfile.TemporaryDirectory() as temporary:
            plan_path = Path(temporary) / "docs/linear-connector-preflight-v1.json"
            plan_path.parent.mkdir()
            plan_path.write_bytes((ROOT / "docs/linear-connector-preflight-v1.json").read_bytes() + b" ")
            with self.assertRaisesRegex(PreflightError, "plan_changed"):
                verify_plan(Path(temporary))
        with tempfile.TemporaryDirectory() as temporary:
            historical = Path(temporary)
            for relative in ("docs/linear-connector-preflight-v1.json",
                             *plan["frozen_file_sha256"]):
                target = historical / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            (historical / "hormuz/_sqlite_schema.py").write_text("SQLITE_SCHEMA_VERSION = 99\n")
            (historical / "hormuz/postgres.py").write_text("POSTGRES_SCHEMA_VERSION = 99\n")
            (historical / "docs/finance-transition-plan-v8.json").write_text("{}\n")
            self.assertEqual(verify_plan(historical)["status"],
                             "offline_linear_preflight_plan_verified")

    def test_all_four_typed_enrollments_and_generic_actions_stop_at_candidate(self):
        for kind in ("Issue", "Project", "Initiative", "Cycle"):
            for action in ("create", "update", "remove"):
                with self.subTest(kind=kind, action=action):
                    headers, raw = signed_request(kind=kind, action=action)
                    result = self.verify(headers, raw)
                    self.assertEqual((result.object_kind, result.object_id),
                                     (kind.lower(), OBJECTS[kind.lower()][0]))
                    self.assertEqual(result.action, action)
                    self.assertEqual(result.organization_id, "tenant-test")
                    self.assertEqual(result.credential_version, "active-v2")
                    self.assertEqual(result.fingerprint_key_version, "fingerprint-test-v1")
                    self.assertEqual(result.team_claim, "signed_claim_matches_enrollment")
                    self.assertNotIn("lifecycle", result.__dataclass_fields__)
                    self.assertNotIn("normalized_state", result.__dataclass_fields__)

    def test_raw_byte_mutation_rejected_before_parser_and_rotation_is_explicit(self):
        headers, raw = signed_request()
        with mock.patch("tools.verify_linear_connector_preflight.decode_source_body",
                        side_effect=AssertionError("parser_called")):
            self.denied("unauthenticated", headers, raw + b" ")
        previous_headers, previous_raw = signed_request(secret=SECRET_PREVIOUS)
        self.assertEqual(self.verify(previous_headers, previous_raw).credential_version, "previous-v1")
        self.denied("unauthenticated", previous_headers, previous_raw, now=NOW + 1001)

    def test_route_workspace_webhook_and_team_are_server_bound(self):
        headers, raw = signed_request()
        with mock.patch("tools.verify_linear_connector_preflight.decode_source_body",
                        side_effect=AssertionError("parser_called")):
            self.denied("forbidden", headers, raw, route="unknown-route")
            disabled = {"synthetic-linear-route": authority(enabled=False)}
            self.denied("forbidden", headers, raw, registry=disabled)
        for change in ({"organizationId": OTHER_TEAM}, {"webhookId": OTHER_TEAM},
                       {"organization_id": "other-tenant"}, {"work_scope_id": "other-work"},
                       {"workScopeId": "other-work"}):
            with self.subTest(change=change):
                alternate, payload = signed_request(body_changes=change)
                self.denied("forbidden", alternate, payload)
        wrong_team, wrong_raw = signed_request(data={"id": OBJECTS["issue"][0], "teamId": OTHER_TEAM})
        self.denied("forbidden", wrong_team, wrong_raw)
        wrong_team_set, wrong_set_raw = signed_request(
            data={"id": OBJECTS["issue"][0], "teamId": TEAM, "teamIds": [TEAM, OTHER_TEAM]})
        self.denied("forbidden", wrong_team_set, wrong_set_raw)
        missing_team, missing_raw = signed_request(data={"id": OBJECTS["issue"][0]})
        self.assertEqual(self.verify(missing_team, missing_raw).team_claim, "unproven")

    def test_registry_rejects_workspace_tenant_and_webhook_ambiguity(self):
        other = authority(route_id="other-route", organization_id="other-tenant",
                          webhook_id="99999999-9999-4999-8999-999999999999")
        with self.assertRaisesRegex(PreflightError, "authority_ambiguous"):
            validate_registry({"synthetic-linear-route": authority(), "other-route": other})
        same_hook = authority(route_id="other-route")
        with self.assertRaisesRegex(PreflightError, "authority_ambiguous"):
            validate_registry({"synthetic-linear-route": authority(), "other-route": same_hook})
        repeated_secret = authority(signing_keys=(SigningKey("active-v2", SECRET_ACTIVE),
                                                  SigningKey("previous-v1", SECRET_ACTIVE, NOW + 1000)))
        with self.assertRaisesRegex(PreflightError, "authority_invalid"):
            validate_registry({"synthetic-linear-route": repeated_secret})
        self.assertNotIn(repr(SECRET_ACTIVE), repr(authority()))

    def test_prepared_registry_is_immutable_and_delivery_never_scans_all_routes(self):
        objects = dict(OBJECTS)
        routes = {"synthetic-linear-route": authority(typed_object_ids=objects)}
        for index in range(1, 1000):
            routes[f"other-route-{index}"] = authority(
                route_id=f"other-route-{index}",
                workspace_id=f"{index:08x}-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                webhook_id=f"{index:08x}-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            )
        prepared = validate_registry(routes)
        objects["issue"] = ()
        routes.clear()
        with self.assertRaises(TypeError):
            prepared.routes["synthetic-linear-route"] = None
        with self.assertRaisesRegex(PreflightError, "authority_invalid"):
            PreparedRegistry({}, _token=object())
        headers, raw = signed_request()
        with mock.patch("tools.verify_linear_connector_preflight.validate_registry",
                        side_effect=AssertionError("registry rescanned")):
            self.assertEqual(self.verify(headers, raw, registry=prepared).object_kind, "issue")
            self.denied("unauthenticated", headers, raw + b" ", registry=prepared)
        with self.assertRaisesRegex(PreflightError, "authority_invalid"):
            authenticate_candidate(
                route_id="synthetic-linear-route", registry=routes,
                headers=headers, raw=raw, now_ms=NOW,
                fingerprint_key=FINGERPRINT_KEY,
                fingerprint_key_version="fingerprint-test-v1",
            )

    def test_typed_enrollment_and_signed_claims_do_not_expand_scope(self):
        unknown_id = "99999999-9999-4999-8999-999999999999"
        headers, raw = signed_request(data={"id": unknown_id, "projectId": OBJECTS["project"][0],
                                             "teamId": TEAM})
        self.denied("forbidden", headers, raw)
        headers, raw = signed_request(data={"id": OBJECTS["issue"][0],
                                             "organization_id": "tenant-test", "teamId": TEAM})
        self.denied("forbidden", headers, raw)
        project_only = authority(typed_object_ids={**OBJECTS, "issue": ()})
        headers, raw = signed_request()
        self.denied("forbidden", headers, raw,
                    registry={"synthetic-linear-route": project_only})

    def test_unsigned_header_mutations_cannot_change_signed_scope_or_body_fingerprint(self):
        headers, raw = signed_request()
        first = self.verify(headers, raw)
        changed = [(name, OTHER_DELIVERY if name == "Linear-Delivery" else value)
                   for name, value in headers]
        second = self.verify(changed, raw)
        self.assertNotEqual(first.delivery_id_hint, second.delivery_id_hint)
        self.assertEqual(first.keyed_body_fingerprint, second.keyed_body_fingerprint)
        changed_event = [(name, "Project" if name == "Linear-Event" else value)
                         for name, value in headers]
        self.denied("unsupported", changed_event, raw)
        timestamp_hint = headers + [("Linear-Timestamp", str(NOW - 1))]
        self.assertEqual(self.verify(timestamp_hint, raw).keyed_body_fingerprint,
                         first.keyed_body_fingerprint)
        other_tenant = authority(organization_id="other-tenant")
        other = self.verify(headers, raw, registry={"synthetic-linear-route": other_tenant})
        self.assertNotEqual(first.keyed_body_fingerprint, other.keyed_body_fingerprint)

    def test_content_is_ignored_and_errors_never_echo_it(self):
        sentinel = "SYNTHETIC_EXCLUDED_CONTENT"
        headers, raw = signed_request(body_changes={"actor": {"name": sentinel},
                                                    "url": f"https://invalid.example/{sentinel}"},
                                      data={"id": OBJECTS["issue"][0], "teamId": TEAM,
                                            "title": sentinel, "description": sentinel})
        self.assertNotIn(sentinel, repr(self.verify(headers, raw)))
        changed = raw.replace(sentinel.encode(), b"CHANGED_EXCLUDED_CONTENT")
        with self.assertRaises(PreflightError) as caught:
            self.verify(headers, changed)
        self.assertEqual(caught.exception.code, "unauthenticated")
        self.assertNotIn(sentinel, str(caught.exception))

    def test_security_header_duplicates_and_signed_timestamp_fail_closed(self):
        headers, raw = signed_request()
        for name in ("Linear-Signature", "Linear-Delivery", "Linear-Event"):
            value = next(value for header, value in headers if header == name)
            with self.subTest(name=name):
                self.denied("invalid_request", headers + [(name.lower(), value)], raw)
        ordinary_duplicates = headers + [("Accept", "application/json"), ("accept", "*/*"),
                                         ("Linear-Timestamp", "0"), ("linear-timestamp", "1")]
        self.assertEqual(self.verify(ordinary_duplicates, raw).object_kind, "issue")
        self.denied("invalid_request", headers + [("Accept", "bad\r\nheader")], raw)
        self.denied("unauthenticated", headers, raw, now=NOW + 60_001)
        self.denied("unauthenticated", headers, raw, now=NOW - 60_001)
        self.denied("invalid_request", headers, b"x" * 1048577)
        unsupported, other_raw = signed_request(kind="Comment")
        self.denied("unsupported", unsupported, other_raw)
        for change in ({"type": ["Issue"]}, {"action": ["create"]},
                       {"webhookTimestamp": True}):
            altered_headers, altered_raw = signed_request(body_changes=change)
            self.denied("unauthenticated" if "webhookTimestamp" in change else "unsupported",
                        altered_headers, altered_raw)
        nonfinite = b'{"webhookTimestamp":NaN}'
        bad_headers = [(name, hmac.new(SECRET_ACTIVE, nonfinite, hashlib.sha256).hexdigest()
                        if name == "Linear-Signature" else value) for name, value in headers]
        self.denied("invalid_request", bad_headers, nonfinite)


if __name__ == "__main__":
    unittest.main()
